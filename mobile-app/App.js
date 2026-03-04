import React, { useMemo, useState } from 'react';
import { SafeAreaView, StatusBar, View, Text, TextInput, TouchableOpacity, FlatList, StyleSheet, KeyboardAvoidingView, Platform, ScrollView } from 'react-native';
import Constants from 'expo-constants';

const defaultMessage = { id: 'welcome', role: 'ai', text: 'Paramedic Assistant initialized. How can I help?' };

const extractHostFromExpo = () => {
  const hostUri = Constants?.expoConfig?.hostUri;
  if (hostUri) return hostUri.split(':')[0];

  const debuggerHost =
    Constants?.expoGoConfig?.debuggerHost ||
    Constants?.manifest2?.extra?.expoGo?.debuggerHost ||
    Constants?.manifest?.debuggerHost;
  if (debuggerHost) return debuggerHost.split(':')[0];

  return null;
};

const isIpv4 = (value) => /^\d{1,3}(\.\d{1,3}){3}$/.test(value || '');

const resolveBaseUrl = () => {
  const configured = Constants?.expoConfig?.extra?.API_BASE_URL?.trim();
  const isLocalhostConfig = configured && /localhost|127\.0\.0\.1/.test(configured);

  if (configured && !isLocalhostConfig) return configured;

  const expoHost = extractHostFromExpo();
  if (isIpv4(expoHost)) return `http://${expoHost}:8000`;

  if (Platform.OS === 'android') return 'http://10.0.2.2:8000';
  return 'http://127.0.0.1:8000';
};

export default function App() {
  const [sessionId] = useState(`mobile_${Date.now()}`);
  const [input, setInput] = useState('');
  const [messages, setMessages] = useState([defaultMessage]);
  const [activeFormData, setActiveFormData] = useState(null);
  const [isLoading, setIsLoading] = useState(false);

  const apiBase = useMemo(resolveBaseUrl, []);

  const appendMessage = (role, text) => {
    setMessages((prev) => [...prev, { id: `${Date.now()}_${Math.random()}`, role, text }]);
  };

  const sendToBackend = async (text) => {
    if (!text?.trim()) return;
    appendMessage('user', text);
    setIsLoading(true);

    try {
      const response = await fetch(`${apiBase}/api/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_id: sessionId, text }),
      });

      if (!response.ok) throw new Error(`Backend returned ${response.status}`);

      const data = await response.json();
      appendMessage('ai', data.ai_audio_reply || 'No response text returned.');
      if (data.form_data) setActiveFormData(data.form_data);
    } catch (err) {
      appendMessage('ai', `Request failed: ${err.message}`);
    } finally {
      setIsLoading(false);
    }
  };

  const onSubmit = async () => {
    const value = input.trim();
    if (!value) return;
    setInput('');
    await sendToBackend(value);
  };

  const quickActions = [
    { label: 'Start Form 1', prompt: 'start form 1 occurrence report' },
    { label: 'Start Form 2', prompt: 'start form 2 teddy bear report' },
    { label: 'Start Form 3', prompt: 'start form 3 shift report' },
    { label: 'Start Form 4', prompt: 'start form 4 status report' },
    { label: 'Reset Task', prompt: 'reset task' },
  ];

  return (
    <SafeAreaView style={styles.container}>
      <StatusBar barStyle="light-content" />
      <KeyboardAvoidingView behavior={Platform.OS === 'ios' ? 'padding' : undefined} style={styles.flex}>
        <View style={styles.header}>
          <Text style={styles.title}>Paramedic Assistant</Text>
          <Text style={styles.subtitle}>Mobile (iOS + Android) · API {apiBase}</Text>
        </View>

        <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.quickWrap}>
          {quickActions.map((item) => (
            <TouchableOpacity key={item.label} style={styles.quickBtn} onPress={() => sendToBackend(item.prompt)}>
              <Text style={styles.quickText}>{item.label}</Text>
            </TouchableOpacity>
          ))}
        </ScrollView>

        <FlatList
          style={styles.list}
          data={messages}
          keyExtractor={(item) => item.id}
          renderItem={({ item }) => (
            <View style={[styles.bubble, item.role === 'user' ? styles.userBubble : styles.aiBubble]}>
              <Text style={styles.bubbleText}>{item.text}</Text>
            </View>
          )}
        />

        <View style={styles.formCard}>
          <Text style={styles.formTitle}>Active Form Data</Text>
          {activeFormData ? (
            Object.entries(activeFormData).map(([key, value]) => (
              <View key={key} style={styles.row}>
                <Text style={styles.key}>{key.replaceAll('_', ' ')}</Text>
                <Text style={styles.value}>{String(value || 'N/A')}</Text>
              </View>
            ))
          ) : (
            <Text style={styles.empty}>Awaiting form completion...</Text>
          )}
        </View>

        <View style={styles.inputWrap}>
          <TextInput
            style={styles.input}
            value={input}
            onChangeText={setInput}
            placeholder="Type your request..."
            placeholderTextColor="#94a3b8"
            onSubmitEditing={onSubmit}
            returnKeyType="send"
          />
          <TouchableOpacity style={[styles.sendBtn, isLoading && styles.sendDisabled]} onPress={onSubmit} disabled={isLoading}>
            <Text style={styles.sendText}>{isLoading ? '...' : 'Send'}</Text>
          </TouchableOpacity>
        </View>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#0f172a' },
  flex: { flex: 1 },
  header: { paddingHorizontal: 16, paddingTop: 10, paddingBottom: 6 },
  title: { color: '#e2e8f0', fontSize: 22, fontWeight: '700' },
  subtitle: { color: '#94a3b8', marginTop: 2 },
  quickWrap: { paddingHorizontal: 12, paddingVertical: 6, gap: 8 },
  quickBtn: { backgroundColor: '#1d4ed8', paddingHorizontal: 12, paddingVertical: 8, borderRadius: 10 },
  quickText: { color: 'white', fontWeight: '600' },
  list: { flex: 1, paddingHorizontal: 12, marginTop: 6 },
  bubble: { maxWidth: '85%', borderRadius: 12, padding: 10, marginBottom: 8 },
  userBubble: { alignSelf: 'flex-end', backgroundColor: '#0284c7' },
  aiBubble: { alignSelf: 'flex-start', backgroundColor: '#334155' },
  bubbleText: { color: 'white', lineHeight: 20 },
  formCard: { margin: 12, backgroundColor: '#1e293b', borderRadius: 12, padding: 12 },
  formTitle: { color: '#e2e8f0', fontWeight: '700', marginBottom: 8 },
  row: { flexDirection: 'row', justifyContent: 'space-between', borderBottomWidth: 1, borderBottomColor: '#334155', paddingVertical: 4 },
  key: { color: '#94a3b8', textTransform: 'capitalize' },
  value: { color: '#e2e8f0', fontWeight: '600', flexShrink: 1, marginLeft: 10, textAlign: 'right' },
  empty: { color: '#94a3b8' },
  inputWrap: { flexDirection: 'row', alignItems: 'center', gap: 8, padding: 12, borderTopWidth: 1, borderTopColor: '#1e293b' },
  input: { flex: 1, backgroundColor: '#1e293b', color: '#e2e8f0', borderRadius: 10, paddingHorizontal: 12, paddingVertical: 10 },
  sendBtn: { backgroundColor: '#0ea5e9', borderRadius: 10, paddingHorizontal: 14, paddingVertical: 10 },
  sendDisabled: { opacity: 0.6 },
  sendText: { color: 'white', fontWeight: '700' },
});
