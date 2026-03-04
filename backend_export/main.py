from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from openai import AsyncOpenAI
import os
import json
import re
from pathlib import Path
from datetime import datetime

# --- IMPORTS FOR YOUR CUSTOM TOOLS ---
# Make sure document_generator.py and schedule_tool.py are in the same folder!
from document_generator import (
    create_teddy_bear_docs,
    create_occurrence_docs,
    create_shift_report_docs,
    email_target_address,
    send_direct_email,
)
from schedule_tool import check_schedule
from status_report_tool import check_status_report
from weather_tool import check_weather


def _load_env_file() -> None:
    env_path = Path(__file__).resolve().parent / ".env"
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _append_jsonl(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _log_short_term(session_id: str, role: str, content: str) -> None:
    log_path = Path(__file__).resolve().parent / "memory" / "short_term" / f"{session_id}.jsonl"
    _append_jsonl(
        log_path,
        {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "session_id": session_id,
            "role": role,
            "content": content,
        },
    )


def _log_long_term(form_type: str, session_id: str, summary: dict) -> None:
    log_path = Path(__file__).resolve().parent / "memory" / "long_term" / f"{form_type}.jsonl"
    _append_jsonl(
        log_path,
        {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "session_id": session_id,
            "form_type": form_type,
            "summary": summary,
        },
    )

# --- 1. SETUP & CONFIG ---
app = FastAPI(title="Paramedic AI Master Brain")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_load_env_file()

OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY")
LLM_MODEL = os.environ.get("OPENROUTER_MODEL", "openai/gpt-5")
client = (
    AsyncOpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=OPENROUTER_API_KEY,
    )
    if OPENROUTER_API_KEY
    else None
)

# In-memory database to remember the conversation for the live demo
chat_memory = {}
current_task = {} 
pending_confirmation = {}
user_profiles = {}

class ChatInput(BaseModel):
    session_id: str = "demo_tablet_1"
    text: str


VALID_GENDERS = {"male", "female", "other", "prefer not to say"}
VALID_RECIPIENT_TYPES = {"patient", "family", "bystander", "other"}
OCCURRENCE_REQUIRED_FIELDS = [
    "date",
    "time",
    "classification",
    "occurrence_type",
    "brief_description",
    "requested_by",
    "report_creator",
]
SHIFT_REQUIRED_FIELDS = [
    "medic_id",
    "date",
    "shift_start",
    "shift_end",
    "station",
    "partner",
    "break_window",
    "vehicle_or_rig",
    "odometer_start",
    "fuel_level",
    "equipment_check_status",
    "initial_notes",
]


def _extract_teddy_fields(memory: list[dict]) -> dict:
    extracted = {}

    for index, message in enumerate(memory):
        if message.get("role") != "user":
            continue

        content = message.get("content", "").lower()

        for age_match in re.finditer(r"\b(\d{1,3})\b", content):
            age_candidate = int(age_match.group(1))
            if 0 <= age_candidate <= 120:
                extracted["age"] = age_candidate

        for gender in ["prefer not to say", "male", "female", "other"]:
            if gender in content:
                extracted["gender"] = "Prefer not to say" if gender == "prefer not to say" else gender.capitalize()

        for recipient in ["patient", "family", "bystander", "other"]:
            if recipient in content:
                extracted["recipient_type"] = recipient.capitalize()

    return extracted


def _extract_occurrence_fields(memory: list[dict], default_name: str = "") -> dict:
    extracted = {}
    classifications = ["safety", "vehicle", "patient", "equipment", "other"]
    occ_types = ["collision", "near miss", "injury", "equipment", "non-call", "other"]

    if default_name:
        extracted["requested_by"] = default_name
        extracted["report_creator"] = default_name

    for index, message in enumerate(memory):
        if message.get("role") != "user":
            continue

        content = message.get("content", "")
        content_l = content.lower()

        date_match = re.search(r"\b(\d{4}-\d{2}-\d{2}|\d{2}/\d{2}/\d{4})\b", content)
        if date_match:
            extracted["date"] = date_match.group(1)

        time_match = re.search(r"\b([01]?\d|2[0-3]):[0-5]\d\b", content)
        if time_match:
            extracted["time"] = time_match.group(0)

        for item in classifications:
            if item in content_l:
                extracted["classification"] = item.title()

        for item in occ_types:
            if item in content_l:
                extracted["occurrence_type"] = item.title()

        call_match = re.search(r"(?:call\s*(?:number|#)\s*)([A-Za-z0-9\-]+)", content, flags=re.IGNORECASE)
        if call_match:
            extracted["call_number"] = call_match.group(1)

        ref_match = re.search(r"(?:reference\s*(?:number|#)?\s*)([A-Za-z0-9\-]+)", content, flags=re.IGNORECASE)
        if ref_match:
            extracted["occurrence_reference"] = ref_match.group(1)

        req_match = re.search(r"requested\s+by\s+([A-Za-z][A-Za-z\s\-']{1,40})", content, flags=re.IGNORECASE)
        if req_match:
            extracted["requested_by"] = req_match.group(1).strip().title()

        creator_match = re.search(r"(?:creator|completed\s+by|report\s+creator)\s+([A-Za-z][A-Za-z\s\-']{1,40})", content, flags=re.IGNORECASE)
        if creator_match:
            extracted["report_creator"] = creator_match.group(1).strip().title()

        if "description" in content_l:
            extracted["brief_description"] = content.strip()
        elif len(content.split()) >= 6 and "brief_description" not in extracted:
            extracted["brief_description"] = content.strip()

    return extracted


def _build_status_response_from_text(text: str) -> tuple[dict, str]:
    normalized = text.lower()
    item_type = None
    status_filter = None
    bad_only = False

    if "bad" in normalized or "outstanding" in normalized or "issue" in normalized:
        bad_only = True
    if "good" in normalized:
        status_filter = "GOOD"

    keywords = [
        "acr", "vaccination", "overtime", "drivers license", "uniform", "vacation", "meals", "acp", "criminal",
    ]
    for keyword in keywords:
        if keyword in normalized:
            item_type = keyword
            break

    status_data_json = check_status_report(item_type=item_type, status_filter=status_filter, bad_only=bad_only)
    parsed = json.loads(status_data_json)

    if not parsed.get("found"):
        ai_reply = "I couldn't find a matching status item. You can ask for all BAD items, vaccination status, ACR status, or overtime status."
    elif parsed.get("count") == 1:
        row = parsed["rows"][0]
        ai_reply = (
            f"{row['item_type']} ({row['description']}) is {row['status']} with {row['issues']} issue(s). "
            f"Note: {row['notes']}"
        )
    else:
        ai_reply = (
            f"I found {parsed['count']} matching items with {parsed['total_issues']} total issues. "
            f"Overall BAD items: {parsed['summary']['bad_items']} totaling {parsed['summary']['bad_issue_total']} issues."
        )

    return parsed, ai_reply


def _extract_shift_fields(memory: list[dict], default_name: str = "") -> dict:
    extracted = {}
    if default_name:
        extracted["report_creator"] = default_name

    for index, message in enumerate(memory):
        if message.get("role") != "user":
            continue

        content = message.get("content", "")
        content_l = content.lower()

        medic_match = re.search(r"(?:medic\s*id|id)\s*[:#-]?\s*(\d{3,8})", content, flags=re.IGNORECASE)
        if medic_match:
            extracted["medic_id"] = int(medic_match.group(1))

        date_match = re.search(r"\b(\d{4}-\d{2}-\d{2}|\d{2}/\d{2}/\d{4})\b", content)
        if date_match:
            extracted["date"] = date_match.group(1)

        time_range = re.search(r"\b([01]?\d|2[0-3]):[0-5]\d\s*[-–]\s*([01]?\d|2[0-3]):[0-5]\d\b", content)
        if time_range:
            parts = re.split(r"\s*[-–]\s*", time_range.group(0))
            if len(parts) == 2:
                extracted["shift_start"] = parts[0]
                extracted["shift_end"] = parts[1]

        station_match = re.search(r"(?:station|base|unit)\s*[:#-]?\s*([A-Za-z0-9\-\s]{2,60})", content, flags=re.IGNORECASE)
        if station_match and "vehicle" not in content_l and "rig" not in content_l:
            extracted["station"] = station_match.group(1).strip()

        partner_match = re.search(r"(?:partner)\s*[:#-]?\s*([A-Za-z0-9\-\s]{2,60})", content, flags=re.IGNORECASE)
        if partner_match:
            extracted["partner"] = partner_match.group(1).strip()

        break_match = re.search(r"(?:break(?:\s*window)?)\s*[:#-]?\s*([0-2]?\d:[0-5]\d\s*[-–]\s*[0-2]?\d:[0-5]\d)", content, flags=re.IGNORECASE)
        if break_match:
            extracted["break_window"] = break_match.group(1).replace(" ", "")

        vehicle_match = re.search(r"(?:vehicle|rig)\s*[:#-]?\s*([A-Za-z0-9\-\s]{2,40})", content, flags=re.IGNORECASE)
        if vehicle_match:
            extracted["vehicle_or_rig"] = vehicle_match.group(1).strip()

        odometer_match = re.search(
            r"(?:odometer(?:\s*start)?(?:\s*reading)?)\b[^\d]{0,20}(\d{2,7})",
            content,
            flags=re.IGNORECASE,
        )
        if odometer_match:
            extracted["odometer_start"] = int(odometer_match.group(1))
        elif re.fullmatch(r"\d{2,7}", content.strip()):
            previous = memory[index - 1] if index > 0 else None
            if previous and previous.get("role") == "assistant" and "odometer" in previous.get("content", "").lower():
                extracted["odometer_start"] = int(content.strip())

        fuel_match = re.search(r"(?:fuel(?:\s*level)?)\s*[:#-]?\s*([A-Za-z0-9%\s]{2,20})", content, flags=re.IGNORECASE)
        if fuel_match:
            extracted["fuel_level"] = fuel_match.group(1).strip().title()
        elif any(token in content_l for token in ["full", "half", "quarter", "empty"]) and "fuel" in content_l:
            if "three quarter" in content_l or "3/4" in content_l:
                extracted["fuel_level"] = "Three Quarter"
            elif "half" in content_l:
                extracted["fuel_level"] = "Half"
            elif "quarter" in content_l:
                extracted["fuel_level"] = "Quarter"
            elif "empty" in content_l:
                extracted["fuel_level"] = "Empty"
            elif "full" in content_l:
                extracted["fuel_level"] = "Full"

        if "equipment" in content_l or "narcotics" in content_l:
            extracted["equipment_check_status"] = content.strip()

        notes_match = re.search(r"(?:notes?|issues?)\s*[:#-]?\s*(.+)$", content, flags=re.IGNORECASE)
        if notes_match:
            extracted["initial_notes"] = notes_match.group(1).strip()
        elif any(token in content_l for token in ["ppe", "radio", "mdt", "supplies"]) and len(content.split()) >= 4:
            extracted["initial_notes"] = content.strip()

        creator_match = re.search(r"(?:report\s*creator|completed\s*by|creator)\s*[:#-]?\s*([A-Za-z][A-Za-z\s\-']{1,40})", content, flags=re.IGNORECASE)
        if creator_match:
            extracted["report_creator"] = creator_match.group(1).strip().title()

    return extracted


def _get_next_shift_question(missing_field: str) -> str:
    prompts = {
        "medic_id": "What is the medic ID for this Form 3 shift report?",
        "date": "What date is this shift report for? Please use YYYY-MM-DD.",
        "shift_start": "What is the shift start time? Please use HH:MM (24-hour).",
        "shift_end": "What is the shift end time? Please use HH:MM (24-hour).",
        "station": "What station or unit is assigned for this shift?",
        "partner": "Who is your shift partner?",
        "break_window": "What is your break window? Please provide HH:MM-HH:MM.",
        "vehicle_or_rig": "What is the vehicle or rig identifier?",
        "odometer_start": "What is the odometer start reading?",
        "fuel_level": "What is the starting fuel level (for example Full, Half, Quarter, or %)?",
        "equipment_check_status": "What is the equipment and narcotics check status?",
        "initial_notes": "Any initial notes or issues (PPE, radio, MDT, supplies)?",
    }
    return prompts.get(missing_field, "Please provide the remaining Form 3 details.")


def _is_confirmation(text: str) -> bool:
    normalized = text.strip().lower()
    return normalized in {"yes", "y", "confirm", "confirmed", "submit", "go ahead", "correct"}


def _is_cancellation(text: str) -> bool:
    normalized = text.strip().lower()
    return normalized in {"no", "n", "cancel", "stop", "edit", "change"}


def _is_reset_command(text: str) -> bool:
    normalized = text.strip().lower()
    commands = {
        "reset",
        "reset task",
        "reset conversation",
        "start over",
        "clear",
        "clear task",
    }
    return normalized in commands


def _extract_location_from_text(text: str) -> str | None:
    normalized = text.strip().lower()
    patterns = [
        r"weather\s+in\s+([a-zA-Z\s]+)",
        r"temperature\s+in\s+([a-zA-Z\s]+)",
        r"forecast\s+for\s+([a-zA-Z\s]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, normalized)
        if match:
            return match.group(1).strip().title()
    return None


def _extract_direct_email_request(text: str) -> dict | None:
    email_match = re.search(
        r"\bsend\s+email\s+to\s+([A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,})\b",
        text,
        flags=re.IGNORECASE,
    )
    if not email_match:
        return None

    target_email = email_match.group(1)
    remainder = text[email_match.end():].strip()

    subject = "Paramedic AI Assistant Message"
    body = "Hello,\n\nThis is a direct email sent from the Paramedic AI assistant.\n\nRegards,\nParamedic AI"

    subject_match = re.search(r"subject\s*:\s*(.+?)(?=\s+body\s*:|$)", remainder, flags=re.IGNORECASE)
    body_match = re.search(r"body\s*:\s*(.+)$", remainder, flags=re.IGNORECASE)

    if subject_match:
        subject = subject_match.group(1).strip()
    if body_match:
        body = body_match.group(1).strip()
    elif remainder and not subject_match:
        body = remainder

    return {"target_email": target_email, "subject": subject, "body": body}


def _keyword_route_intent(text: str) -> str | None:
    normalized = text.lower()
    if any(token in normalized for token in ["form 1", "occurrence report", "start form 1", "incident report"]):
        return "form_occurrence_report"
    if any(token in normalized for token in ["teddy", "teddy bear", "form 2", "teddy bear tracking"]):
        return "form_teddy_bear"
    if any(token in normalized for token in ["schedule", "shift", "break", "partner", "form 3", "shift report", "online paramedic shift report"]):
        return "form_shift_query"
    if any(token in normalized for token in ["status", "checklist", "acr", "vaccination", "overtime", "form 4", "paramedic status report"]):
        return "form_status_query"
    if any(token in normalized for token in ["weather", "temperature", "forecast"]):
        return "form_weather_query"
    return None


def _extract_name_from_text(text: str) -> str | None:
    patterns = [
        r"\bmy name is\s+([a-zA-Z][a-zA-Z\s\-']{0,40})$",
        r"\bi am\s+([a-zA-Z][a-zA-Z\s\-']{0,40})$",
        r"\bcall me\s+([a-zA-Z][a-zA-Z\s\-']{0,40})$",
    ]
    stripped = text.strip()
    for pattern in patterns:
        match = re.search(pattern, stripped, flags=re.IGNORECASE)
        if match:
            return match.group(1).strip().title()
    return None


def _is_name_query(text: str) -> bool:
    normalized = text.strip().lower()
    direct_patterns = [
        "what is my name",
        "what's my name",
        "whats my name",
        "do you know my name",
        "who am i",
    ]
    if any(pattern in normalized for pattern in direct_patterns):
        return True
    return False


def _looks_like_teddy_data(text: str) -> bool:
    normalized = text.lower()
    if re.search(r"\b\d{1,3}\b", normalized):
        return True
    keywords = [
        "age",
        "gender",
        "male",
        "female",
        "prefer not to say",
        "recipient",
        "patient",
        "family",
        "bystander",
        "other",
        "name",
    ]
    return any(keyword in normalized for keyword in keywords)


def _looks_like_occurrence_data(text: str) -> bool:
    normalized = text.lower()
    patterns = [
        r"\b\d{4}-\d{2}-\d{2}\b",
        r"\b\d{2}/\d{2}/\d{4}\b",
        r"\b([01]?\d|2[0-3]):[0-5]\d\b",
    ]
    if any(re.search(pattern, normalized) for pattern in patterns):
        return True

    keywords = [
        "classification",
        "occurrence",
        "incident",
        "description",
        "requested by",
        "report creator",
        "call number",
        "reference",
        "collision",
        "injury",
        "equipment",
    ]
    return any(keyword in normalized for keyword in keywords)


def _is_unrelated_form_interrupt(task: str, text: str, has_pending_confirmation: bool) -> bool:
    if has_pending_confirmation:
        return False

    normalized = text.strip().lower()
    if not normalized:
        return False

    unrelated_signals = [
        "weather",
        "temperature",
        "forecast",
        "schedule",
        "shift",
        "status",
        "checklist",
        "form 3",
        "form 4",
        "send email",
        "email",
        "what",
        "how",
        "why",
        "when",
        "where",
        "who",
        "help",
    ]

    if not any(signal in normalized for signal in unrelated_signals):
        return False

    if task == "form_teddy_bear":
        return not _looks_like_teddy_data(text)

    if task == "form_occurrence_report":
        return not _looks_like_occurrence_data(text)

    return False


def _is_explicit_intent_switch(text: str) -> bool:
    normalized = text.strip().lower()
    if normalized.startswith(("start form", "switch to", "go to form", "open form", "use form")):
        return True
    explicit_phrases = {
        "form 1",
        "form 2",
        "form 3",
        "form 4",
        "start form 1 occurrence report",
        "start form 2 teddy bear report",
        "start form 3 shift report",
        "start form 4 status report",
        "weather",
        "check weather",
        "check status report",
        "check schedule",
    }
    return normalized in explicit_phrases


@app.get("/api/history/{session_id}")
async def get_chat_history(session_id: str):
    history = chat_memory.get(session_id, [])
    return {"session_id": session_id, "history": history}


@app.get("/api/live")
async def get_live_context(location: str | None = None):
    weather_json = check_weather(location)
    weather = json.loads(weather_json)
    return {
        "server_time": datetime.now().isoformat(timespec="seconds"),
        "weather": weather,
    }

# --- 2. TOOLS & SCHEMAS ---
class TeddyBearForm(BaseModel):
    name: str | None = Field(default=None, description="The recipient or patient name, if provided.")
    age: int = Field(description="The age of the recipient in years.")
    gender: str = Field(description="The gender of the recipient. Must be: Male, Female, Other, or Prefer not to say.")
    recipient_type: str = Field(description="Who received the bear. Must be: Patient, Family, Bystander, or Other.")


class OccurrenceForm(BaseModel):
    date: str = Field(description="Occurrence date in YYYY-MM-DD or MM/DD/YYYY format.")
    time: str = Field(description="Occurrence time in HH:MM 24-hour format.")
    classification: str = Field(description="Classification of occurrence.")
    occurrence_type: str = Field(description="Type of occurrence.")
    brief_description: str = Field(description="Short summary of the occurrence.")
    requested_by: str = Field(description="Name of requester.")
    report_creator: str = Field(description="Name of person completing the form.")
    call_number: str | None = Field(default=None, description="Call number if available.")
    occurrence_reference: str | None = Field(default=None, description="Occurrence reference if available.")

TEDDY_BEAR_TOOL = {
    "type": "function",
    "function": {
        "name": "submit_teddy_bear_form",
        "description": "Call this ONLY when you have Age, Gender, and Recipient Type.",
        "parameters": TeddyBearForm.model_json_schema()
    }
}

OCCURRENCE_TOOL = {
    "type": "function",
    "function": {
        "name": "submit_occurrence_form",
        "description": "Call this when required Form 1 occurrence fields are collected.",
        "parameters": OccurrenceForm.model_json_schema(),
    },
}

SCHEDULE_TOOL = {
    "type": "function",
    "function": {
        "name": "check_schedule",
        "description": "Call this to look up shift details, break times, or partners for a paramedic.",
        "parameters": {
            "type": "object",
            "properties": {
                "medic_id": {"type": "integer", "description": "The Medic's ID (e.g., 10452)"},
                "target_date": {"type": "string", "description": "The date in YYYY-MM-DD format. Optional."}
            },
            "required": ["medic_id"]
        }
    }
}

STATUS_REPORT_TOOL = {
    "type": "function",
    "function": {
        "name": "check_status_report",
        "description": "Call this to answer questions about paramedic checklist and status report items.",
        "parameters": {
            "type": "object",
            "properties": {
                "item_type": {"type": "string", "description": "Specific checklist item type or description keyword. Optional."},
                "status_filter": {"type": "string", "description": "Filter by status, e.g., GOOD or BAD. Optional."},
                "bad_only": {"type": "boolean", "description": "If true, return only BAD items."},
            },
            "required": []
        }
    }
}

# --- 3. THE CORE ENDPOINT ---
@app.post("/api/chat")
async def process_chat(input_data: ChatInput):
    session = input_data.session_id
    user_text = input_data.text

    # Initialize memory if this is a new session
    if session not in chat_memory:
        chat_memory[session] = []
        current_task[session] = "routing" 

    if session not in pending_confirmation:
        pending_confirmation[session] = None

    if session not in user_profiles:
        user_profiles[session] = {"name": os.getenv("PARAMEDIC_NAME", "").strip()}

    # Add user's new message to the memory
    chat_memory[session].append({"role": "user", "content": user_text})
    _log_short_term(session, "user", user_text)

    if _is_reset_command(user_text):
        chat_memory[session] = []
        current_task[session] = "routing"
        pending_confirmation[session] = None
        reply = "Session reset. I am back in routing mode. Tell me what you want to do next."
        chat_memory[session].append({"role": "assistant", "content": reply})
        _log_short_term(session, "assistant", reply)
        return {"status": "chat", "ai_audio_reply": reply}

    explicit_name = _extract_name_from_text(user_text)
    if explicit_name:
        user_profiles[session]["name"] = explicit_name
        reply = f"Got it. I will call you {explicit_name}."
        chat_memory[session].append({"role": "assistant", "content": reply})
        _log_short_term(session, "assistant", reply)
        _log_long_term("profile", session, {"name": explicit_name})
        return {"status": "chat", "ai_audio_reply": reply}

    if _is_name_query(user_text):
        known_name = user_profiles[session].get("name")
        if known_name:
            reply = f"Your name is {known_name}."
        else:
            reply = "I do not have your name yet. Say: my name is <your name>."
        chat_memory[session].append({"role": "assistant", "content": reply})
        _log_short_term(session, "assistant", reply)
        return {"status": "chat", "ai_audio_reply": reply}

    direct_email_request = _extract_direct_email_request(user_text)
    if direct_email_request:
        email_result = send_direct_email(
            direct_email_request["target_email"],
            direct_email_request["subject"],
            direct_email_request["body"],
        )
        if email_result.sent:
            reply = f"{email_result.detail}."
            status = "complete"
        else:
            reply = f"I couldn't send that email yet: {email_result.detail}"
            status = "chat"

        chat_memory[session].append({"role": "assistant", "content": reply})
        _log_short_term(session, "assistant", reply)
        return {
            "status": status,
            "ai_audio_reply": reply,
            "email_result": {
                "sent": email_result.sent,
                "detail": email_result.detail,
                "to": direct_email_request["target_email"],
            },
        }

    override_intent = _keyword_route_intent(user_text)
    if (
        current_task[session] != "routing"
        and override_intent
        and current_task[session] != override_intent
        and _is_explicit_intent_switch(user_text)
        and pending_confirmation[session] is None
    ):
        current_task[session] = override_intent

    try:
        # ==========================================
        # AGENT 1: THE DISPATCHER (ROUTER)
        # ==========================================
        if current_task[session] == "routing":
            keyword_intent = _keyword_route_intent(user_text)
            if keyword_intent:
                current_task[session] = keyword_intent

            if not client:
                normalized = user_text.lower()
                if not current_task[session] or current_task[session] == "routing":
                    fallback_reply = "I can help with occurrence reports (Form 1), teddy bear tracking (Form 2), shift/schedule (Form 3), status reports (Form 4), weather, and general operational Q&A."
                    chat_memory[session].append({"role": "assistant", "content": fallback_reply})
                    _log_short_term(session, "assistant", fallback_reply)
                    return {"status": "chat", "ai_audio_reply": fallback_reply}

            dispatcher_prompt = """
            You are a router. Read the user's text. 
            If they want Form 1 occurrence report or incident report, reply with exactly: OCCURRENCE_REPORT
            If they want to log a teddy bear or ask for Form 2, reply with exactly: TEDDY_BEAR
            If they ask about their schedule, breaks, shift, or Form 3, reply with exactly: CHECK_SHIFT
            If they ask about checklist, status, ACR, vaccination, overtime, or Form 4 status report, reply with exactly: CHECK_STATUS
            If they ask about weather, temperature, or forecast, reply with exactly: CHECK_WEATHER
            For all other user questions, reply with exactly: GENERAL_CHAT
            """

            if client and (current_task[session] == "routing"):
                response = await client.chat.completions.create(
                    model=LLM_MODEL,
                    messages=[{"role": "system", "content": dispatcher_prompt}, {"role": "user", "content": user_text}]
                )

                ai_decision = response.choices[0].message.content.strip()

                if "TEDDY_BEAR" in ai_decision:
                    current_task[session] = "form_teddy_bear"
                elif "OCCURRENCE_REPORT" in ai_decision:
                    current_task[session] = "form_occurrence_report"
                elif "CHECK_SHIFT" in ai_decision:
                    current_task[session] = "form_shift_query"
                elif "CHECK_STATUS" in ai_decision:
                    current_task[session] = "form_status_query"
                elif "CHECK_WEATHER" in ai_decision:
                    current_task[session] = "form_weather_query"
                elif "GENERAL_CHAT" in ai_decision:
                    general_prompt = {
                        "role": "system",
                        "content": "You are a friendly paramedic AI assistant. Answer clearly and briefly. If asked for live external data you do not have, say so and provide a useful alternative.",
                    }
                    general_response = await client.chat.completions.create(
                        model=LLM_MODEL,
                        messages=[general_prompt, {"role": "user", "content": user_text}],
                    )
                    general_reply = general_response.choices[0].message.content.strip()
                    chat_memory[session].append({"role": "assistant", "content": general_reply})
                    _log_short_term(session, "assistant", general_reply)
                    return {"status": "chat", "ai_audio_reply": general_reply}
                else:
                    fallback_reply = "I can help with Form 1 occurrence reports, Form 2 teddy bear tracking, Form 3 shift/schedule, Form 4 status reports, weather, and general operational Q&A."
                    chat_memory[session].append({"role": "assistant", "content": fallback_reply})
                    _log_short_term(session, "assistant", fallback_reply)
                    return {"status": "chat", "ai_audio_reply": fallback_reply}

        # ==========================================
        # AGENT 1B: OCCURRENCE REPORT (FORM 1)
        # ==========================================
        if current_task[session] == "form_occurrence_report":
            if _is_unrelated_form_interrupt("form_occurrence_report", user_text, pending_confirmation[session] is not None):
                current_task[session] = "routing"
                pending_confirmation[session] = None
                reply = "I paused Form 1 because your latest request looks unrelated. How can I help with this request?"
                chat_memory[session].append({"role": "assistant", "content": reply})
                _log_short_term(session, "assistant", reply)
                return {"status": "chat", "ai_audio_reply": reply}

            if pending_confirmation[session]:
                if _is_confirmation(user_text):
                    extracted_data = pending_confirmation[session]
                    printable_path, xml_path = create_occurrence_docs(extracted_data)
                    email_result = email_target_address(printable_path, xml_path)

                    _log_long_term(
                        "occurrence_report",
                        session,
                        {
                            "form_data": extracted_data,
                            "artifacts": {"printable_path": printable_path, "xml_path": xml_path},
                            "email_result": {"sent": email_result.sent, "detail": email_result.detail},
                        },
                    )

                    pending_confirmation[session] = None
                    chat_memory[session] = []
                    current_task[session] = "routing"

                    success_msg = f"Occurrence report submitted. {email_result.detail}"
                    _log_short_term(session, "assistant", success_msg)
                    return {
                        "status": "complete",
                        "form_data": extracted_data,
                        "artifacts": {"printable_path": printable_path, "xml_path": xml_path},
                        "ai_audio_reply": success_msg,
                    }

                if _is_cancellation(user_text):
                    pending_confirmation[session] = None
                    chat_memory[session] = []
                    ask = "No problem. Please provide corrected occurrence report details."
                    _log_short_term(session, "assistant", ask)
                    return {"status": "collecting", "ai_audio_reply": ask}

                ask = "Please say confirm to submit the occurrence report, or cancel to edit."
                _log_short_term(session, "assistant", ask)
                return {"status": "collecting", "ai_audio_reply": ask}

            if not client:
                extracted_data = _extract_occurrence_fields(chat_memory[session], user_profiles[session].get("name", ""))
                missing = [field for field in OCCURRENCE_REQUIRED_FIELDS if field not in extracted_data]

                if missing:
                    prompts = {
                        "date": "What is the occurrence date?",
                        "time": "What is the occurrence time?",
                        "classification": "What is the classification (Safety, Vehicle, Patient, Equipment, Other)?",
                        "occurrence_type": "What is the occurrence type (Collision, Near Miss, Injury, Equipment, Non-call, Other)?",
                        "brief_description": "Please provide a brief description of the occurrence.",
                        "requested_by": "Who requested this report?",
                        "report_creator": "Who is completing this report?",
                    }
                    ask = prompts[missing[0]]
                    _log_short_term(session, "assistant", ask)
                    return {"status": "collecting", "ai_audio_reply": ask}

                pending_confirmation[session] = extracted_data
                confirm_msg = (
                    f"I captured Form 1: date {extracted_data['date']}, time {extracted_data['time']}, "
                    f"classification {extracted_data['classification']}, type {extracted_data['occurrence_type']}, "
                    f"requested by {extracted_data['requested_by']}, creator {extracted_data['report_creator']}. "
                    f"Say confirm to submit or cancel to edit."
                )
                _log_short_term(session, "assistant", confirm_msg)
                return {"status": "confirm", "form_data": extracted_data, "ai_audio_reply": confirm_msg}

            agent_1b_prompt = {
                "role": "system",
                "content": """
                You are collecting Form 1 Occurrence Report.
                Required fields: date, time, classification, occurrence_type, brief_description, requested_by, report_creator.
                Ask concise follow-up questions for missing fields.
                When all required fields are available, call submit_occurrence_form.
                """,
            }

            messages = [agent_1b_prompt] + chat_memory[session]
            response = await client.chat.completions.create(
                model=LLM_MODEL,
                messages=messages,
                tools=[OCCURRENCE_TOOL],
                tool_choice="auto",
            )
            message = response.choices[0].message

            if message.tool_calls:
                extracted_data = json.loads(message.tool_calls[0].function.arguments)
                if not extracted_data.get("requested_by") and user_profiles[session].get("name"):
                    extracted_data["requested_by"] = user_profiles[session]["name"]
                if not extracted_data.get("report_creator") and user_profiles[session].get("name"):
                    extracted_data["report_creator"] = user_profiles[session]["name"]

                pending_confirmation[session] = extracted_data
                confirm_msg = (
                    f"I captured Form 1: date {extracted_data['date']}, time {extracted_data['time']}, "
                    f"classification {extracted_data['classification']}, type {extracted_data['occurrence_type']}, "
                    f"requested by {extracted_data['requested_by']}, creator {extracted_data['report_creator']}. "
                    f"Say confirm to submit or cancel to edit."
                )
                _log_short_term(session, "assistant", confirm_msg)
                return {"status": "confirm", "form_data": extracted_data, "ai_audio_reply": confirm_msg}

            ai_reply = message.content
            _log_short_term(session, "assistant", ai_reply)
            return {"status": "collecting", "ai_audio_reply": ai_reply}

        # ==========================================
        # AGENT 5: THE WEATHER ANALYST
        # ==========================================
        if current_task[session] == "form_weather_query":
            location = _extract_location_from_text(user_text)
            weather_json = check_weather(location)
            parsed = json.loads(weather_json)

            ai_reply = (
                f"Current weather in {parsed['location']}: {parsed['current_condition']}, "
                f"{parsed['current_temperature_c']}°C, wind {parsed['current_windspeed_kmh']} km/h. "
                f"Today ranges from {parsed['today_min_c']}°C to {parsed['today_max_c']}°C "
                f"with up to {parsed['today_precip_probability_max']}% precipitation chance."
            )

            _log_long_term("weather", session, parsed)
            chat_memory[session] = []
            current_task[session] = "routing"
            _log_short_term(session, "assistant", ai_reply)
            return {"status": "complete", "weather": parsed, "ai_audio_reply": ai_reply}

        # ==========================================
        # AGENT 2: THE WRITER (TEDDY BEAR FORM)
        # ==========================================
        if current_task[session] == "form_teddy_bear":
            if _is_unrelated_form_interrupt("form_teddy_bear", user_text, pending_confirmation[session] is not None):
                current_task[session] = "routing"
                pending_confirmation[session] = None
                normalized = user_text.lower()
                if "email" in normalized:
                    reply = "I paused Form 2. I can send email attachments after a form is submitted. What would you like to do next?"
                else:
                    reply = "I paused Form 2 because your latest request looks unrelated. How can I help with this request?"
                chat_memory[session].append({"role": "assistant", "content": reply})
                _log_short_term(session, "assistant", reply)
                return {"status": "chat", "ai_audio_reply": reply}

            if pending_confirmation[session]:
                if _is_confirmation(user_text):
                    extracted_data = pending_confirmation[session]
                    pdf_path, xml_path = create_teddy_bear_docs(extracted_data)
                    email_result = email_target_address(pdf_path, xml_path)

                    _log_long_term(
                        "teddy_bear",
                        session,
                        {
                            "form_data": extracted_data,
                            "artifacts": {"printable_path": pdf_path, "xml_path": xml_path},
                            "email_result": {"sent": email_result.sent, "detail": email_result.detail},
                        },
                    )

                    pending_confirmation[session] = None
                    chat_memory[session] = []
                    current_task[session] = "routing"

                    success_msg = (
                        f"Perfect. I submitted the report for a {extracted_data['age']} year old "
                        f"{extracted_data['gender']} {extracted_data['recipient_type']}. {email_result.detail}"
                    )
                    _log_short_term(session, "assistant", success_msg)
                    return {
                        "status": "complete",
                        "form_data": extracted_data,
                        "artifacts": {"printable_path": pdf_path, "xml_path": xml_path},
                        "ai_audio_reply": success_msg,
                    }

                if _is_cancellation(user_text):
                    pending_confirmation[session] = None
                    chat_memory[session] = []
                    ask = "No problem. Please tell me the corrected age, gender, and recipient type."
                    _log_short_term(session, "assistant", ask)
                    return {"status": "collecting", "ai_audio_reply": ask}

                ask = "Please say confirm to submit, or cancel to edit the teddy bear report."
                _log_short_term(session, "assistant", ask)
                return {"status": "collecting", "ai_audio_reply": ask}

            if not client:
                extracted_data = _extract_teddy_fields(chat_memory[session])
                if user_profiles[session].get("name"):
                    extracted_data["name"] = user_profiles[session]["name"]
                missing = [
                    field for field in ["name", "age", "gender", "recipient_type"] if field not in extracted_data
                ]
                if missing:
                    prompts = {
                        "name": "What is the recipient name?",
                        "age": "What is the recipient age?",
                        "gender": "What is the recipient gender (Male, Female, Other, Prefer not to say)?",
                        "recipient_type": "Who received it (Patient, Family, Bystander, Other)?",
                    }
                    ask = prompts[missing[0]]
                    chat_memory[session].append({"role": "assistant", "content": ask})
                    _log_short_term(session, "assistant", ask)
                    return {"status": "collecting", "ai_audio_reply": ask}

                pending_confirmation[session] = extracted_data
                confirm_msg = (
                    f"I captured: name {extracted_data.get('name', 'N/A')}, age {extracted_data['age']}, "
                    f"gender {extracted_data['gender']}, recipient type {extracted_data['recipient_type']}. "
                    f"Say confirm to submit or cancel to edit."
                )
                _log_short_term(session, "assistant", confirm_msg)
                return {
                    "status": "confirm",
                    "form_data": extracted_data,
                    "ai_audio_reply": confirm_msg,
                }

            agent_2_prompt = {"role": "system", "content": """
            You are collecting data for the Teddy Bear Form. You need: 1. Name, 2. Age, 3. Gender, 4. Recipient Type.
            Look at the conversation history. If missing any of these, ask a friendly, brief follow-up question.
            If you have all three, use the submit_teddy_bear_form tool.
            """}
            
            messages = [agent_2_prompt] + chat_memory[session]
            
            response = await client.chat.completions.create(
                model=LLM_MODEL,
                messages=messages,
                tools=[TEDDY_BEAR_TOOL],
                tool_choice="auto"
            )
            
            message = response.choices[0].message

            if message.tool_calls:
                # 1. Extract the perfect JSON data
                extracted_data = json.loads(message.tool_calls[0].function.arguments)
                if not extracted_data.get("name") and user_profiles[session].get("name"):
                    extracted_data["name"] = user_profiles[session]["name"]

                if not extracted_data.get("name"):
                    ask = "What is the recipient name?"
                    chat_memory[session].append({"role": "assistant", "content": ask})
                    _log_short_term(session, "assistant", ask)
                    return {"status": "collecting", "ai_audio_reply": ask}

                pending_confirmation[session] = extracted_data
                confirm_msg = (
                    f"I captured: name {extracted_data.get('name', 'N/A')}, age {extracted_data['age']}, "
                    f"gender {extracted_data['gender']}, recipient type {extracted_data['recipient_type']}. "
                    f"Say confirm to submit or cancel to edit."
                )
                chat_memory[session].append({"role": "assistant", "content": confirm_msg})
                _log_short_term(session, "assistant", confirm_msg)
                return {
                    "status": "confirm",
                    "form_data": extracted_data,
                    "ai_audio_reply": confirm_msg,
                }
            else:
                ai_reply = message.content
                chat_memory[session].append({"role": "assistant", "content": ai_reply})
                _log_short_term(session, "assistant", ai_reply)
                return {"status": "collecting", "ai_audio_reply": ai_reply}

        # ==========================================
        # AGENT 3: THE SHIFT ANALYST (FORM 3)
        # ==========================================
        if current_task[session] == "form_shift_query":
            if not client:
                shift_data_json = check_schedule(10452, None)
                parsed = json.loads(shift_data_json)
                if parsed.get("found") and parsed.get("schedule"):
                    shift = parsed["schedule"][0]
                    ai_reply = (
                        f"You are scheduled on {shift['date']} from {shift['shift_start']} to {shift['shift_end']} "
                        f"at {shift['station']} with {shift['partner']}. Break window is {shift['break_window']}."
                    )
                else:
                    ai_reply = parsed.get("message", "I couldn't find your schedule.")

                _log_long_term("shift", session, parsed)

                chat_memory[session] = []
                current_task[session] = "routing"
                _log_short_term(session, "assistant", ai_reply)
                return {"status": "complete", "ai_audio_reply": ai_reply}

            agent_3_prompt = {"role": "system", "content": """
            You are the Shift Analyst. Answer the paramedic's question about their schedule.
            Assume the paramedic speaking is Medic ID 10452 unless they specify otherwise.
            Use the check_schedule tool to get the data, then provide a brief, conversational answer.
            Do not ask for additional form fields or start form-filling steps.
            """}

            messages = [agent_3_prompt] + chat_memory[session]

            response = await client.chat.completions.create(
                model=LLM_MODEL,
                messages=messages,
                tools=[SCHEDULE_TOOL],
                tool_choice="auto"
            )

            message = response.choices[0].message

            if message.tool_calls:
                tool_call = message.tool_calls[0]
                args = json.loads(tool_call.function.arguments)

                shift_data_json = check_schedule(args.get("medic_id", 10452), args.get("target_date"))

                messages.append(message)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": shift_data_json,
                    }
                )

                final_response = await client.chat.completions.create(
                    model=LLM_MODEL,
                    messages=messages,
                )

                ai_reply = final_response.choices[0].message.content
                _log_long_term("shift", session, json.loads(shift_data_json))

                chat_memory[session] = []
                current_task[session] = "routing"
                _log_short_term(session, "assistant", ai_reply)

                return {"status": "complete", "ai_audio_reply": ai_reply}
            else:
                _log_short_term(session, "assistant", message.content)
                return {"status": "chat", "ai_audio_reply": message.content}

        # ==========================================
        # AGENT 4: THE STATUS ANALYST (FORM 4)
        # ==========================================
        if current_task[session] == "form_status_query":
            if not client:
                parsed, ai_reply = _build_status_response_from_text(user_text)

                _log_long_term("status_report", session, parsed)

                chat_memory[session] = []
                current_task[session] = "routing"
                _log_short_term(session, "assistant", ai_reply)
                return {"status": "complete", "status_report": parsed, "ai_audio_reply": ai_reply}

            agent_4_prompt = {"role": "system", "content": """
            You are the Status Analyst for Form 4. Use the check_status_report tool to answer checklist/status questions.
            Keep responses concise and operational. Mention issue counts and notes when relevant.
            """}

            messages = [agent_4_prompt] + chat_memory[session]

            try:
                response = await client.chat.completions.create(
                    model=LLM_MODEL,
                    messages=messages,
                    tools=[STATUS_REPORT_TOOL],
                    tool_choice="auto"
                )

                message = response.choices[0].message

                if message.tool_calls:
                    tool_call = message.tool_calls[0]
                    args = json.loads(tool_call.function.arguments or "{}")

                    status_data_json = check_status_report(
                        item_type=args.get("item_type"),
                        status_filter=args.get("status_filter"),
                        bad_only=bool(args.get("bad_only", False)),
                    )

                    messages.append(message)
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "content": status_data_json,
                        }
                    )

                    final_response = await client.chat.completions.create(
                        model=LLM_MODEL,
                        messages=messages
                    )

                    ai_reply = final_response.choices[0].message.content
                    parsed = json.loads(status_data_json)
                else:
                    parsed, ai_reply = _build_status_response_from_text(user_text)
            except Exception:
                parsed, ai_reply = _build_status_response_from_text(user_text)

            _log_long_term("status_report", session, parsed)
            chat_memory[session] = []
            current_task[session] = "routing"
            _log_short_term(session, "assistant", ai_reply)

            return {
                "status": "complete",
                "status_report": parsed,
                "ai_audio_reply": ai_reply,
            }

    except Exception as e:
        current_task[session] = "routing"
        pending_confirmation[session] = None
        fallback_reply = "I hit a temporary backend issue. Please try again. If this keeps happening, say reset task and retry your request."
        chat_memory[session].append({"role": "assistant", "content": fallback_reply})
        _log_short_term(session, "assistant", f"{fallback_reply} [error: {str(e)}]")
        return {
            "status": "chat",
            "ai_audio_reply": fallback_reply,
            "error": "temporary_backend_issue",
        }