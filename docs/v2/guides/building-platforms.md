# Guide: Building on the Substrate

This guide explains how to build a custom application (like "BreachLens") on top of the Cognition Engine using the REST API.

## 1. The Deployment

Before writing code, you need the Engine running.

```bash
# Production Mode (Docker)
docker-compose up -d
```

Ensure the engine is accessible at `http://localhost:8000`.

## 2. Defining the Agent Persona

Your application's "Soul" is defined by the **System Prompt**. You configure this when creating a session.

### Example: The Forensic Analyst
```typescript
const SYSTEM_PROMPT = `
You are a Senior Forensic Analyst for BreachLens.
Your goal is to investigate security incidents with precision.

HARD RULES:
1. NEVER modify evidence files. Read-only access only.
2. ALWAYS verify findings with a second tool (e.g., verify 'grep' results with 'python').
3. Report high-confidence IOCs immediately.
`;
```

## 3. The Integration Loop

A typical platform interaction loop looks like this:

### Step A: Create a Case (Session)

Start a new thread for the user's task.

```typescript
// POST /sessions
const response = await fetch('http://localhost:8000/sessions', {
  method: 'POST',
  body: JSON.stringify({
    title: 'Case #101: Suspicious PDF',
    config: {
      system_prompt: SYSTEM_PROMPT
    }
  })
});

const session = await response.json();
const sessionId = session.id;
```

### Step B: Send Instructions

Send the user's intent to the Agent.

```typescript
// POST /sessions/:id/messages
const eventSource = new EventSource(
  `http://localhost:8000/sessions/${sessionId}/messages`
);

// Payload needs to be sent via POST, but standard EventSource is GET only.
// Recommended: Use 'fetch' with a readable stream reader instead of EventSource.
```

**Recommended Streaming Pattern (fetch):**

```typescript
const response = await fetch(`http://localhost:8000/sessions/${sessionId}/messages`, {
  method: 'POST',
  body: JSON.stringify({
    content: "Analyze /mnt/evidence/invoice.pdf for malicious JS."
  })
});

const reader = response.body.getReader();
const decoder = new TextDecoder();

while (true) {
  const { value, done } = await reader.read();
  if (done) break;
  
  const chunk = decoder.decode(value);
  // Parse SSE format (event: type\ndata: json\n\n)
  handleSSE(chunk); 
}
```

### Step C: Handle Events

The Engine emits granular events. Your UI should render them to build trust.

| Event Type | UI Representation | Data |
| :--- | :--- | :--- |
| `token` | Streaming text (Typewriter effect) | `{"content": "I am..."}` |
| `tool_call` | "Loading" Spinner or Terminal View | `{"name": "execute", "args": "pdf-parser..."}` |
| `tool_result` | Collapsible "Output" block | `{"output": "JavaScript found..."}` |
| `done` | Remove spinners, enable input | `{}` |

### Step D: Visualize "Thinking"

For a high-trust platform like BreachLens, do not hide the tool usage. Show it.

**Good UX Pattern:**
> 🤖 **Analyst Agent**
>
> I'll check the PDF structure.
>
> 💻 `Executing: pdf-parser.py -s javascript invoice.pdf`
>
> 📄 **Result:** `Object 14 contains JS stream...`
>
> 🚨 **Finding:** This PDF contains an embedded script that attempts to download a payload.

## 4. Handling State

Since Cognition handles persistence, you don't need to save the chat history in your own DB.

**On Page Load:**
Simply query the history from Cognition.

```typescript
// GET /sessions/:id/messages
const history = await fetch(`http://localhost:8000/sessions/${sessionId}/messages`);
renderChat(history);
```

## 5. Security Best Practices

1.  **API Gateway:** Do not expose Cognition directly to the public internet. Put it behind an API Gateway (Nginx/Kong) that handles Authentication (OAuth2) and Rate Limiting.
2.  **Volume Hygiene:** Ensure your platform cleans up evidence volumes after a Session is archived.
3.  **Audit Exports:** Periodically query the Trace data (via Jaeger API) and archive it to cold storage for long-term compliance.
