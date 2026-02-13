Below is a **markdown wireframe** for an **OpenCode-style TUI** (terminal UI) with **Mermaid diagrams** for layout + navigation. Copy/paste into your repo as something like `docs/tui-wireframe.md`.

---

# OpenCode-Style TUI Wireframe

## Goals

* Fast keyboard-driven workflow
* “Chat + Workspace” side-by-side
* Clear session/task context
* Safe tool execution via approvals/interrupts
* Built-in file/tree + diffs + logs

---

## Screen Layout (Primary)

### ASCII Wireframe

```
┌───────────────────────────────────────────────────────────────────────────────┐
│ OpenCode TUI  | Session: "investigations-llm" | Model: gpt-4.1 | ⏺ Recording  │
├───────────────┬───────────────────────────────────────────────┬───────────────┤
│ SIDEBAR       │ CHAT / ORCHESTRATION                           │ WORKSPACE     │
│ (20-25 cols)  │ (flex)                                         │ (35-45 cols)  │
│               │                                                │               │
│ Sessions      │ Assistant                                      │ Files         │
│  > today      │  - plan                                        │  src/         │
│  - yesterday  │  - tool calls                                  │   main.py     │
│  - archived   │                                                │   agent/      │
│               │ User                                           │   tests/      │
│ Tasks         │  prompt...                                     │               │
│  [ ] build    │                                                │ Preview       │
│  [~] test     │ ────────────────────────────────              │  (tabbed)     │
│  [!] deploy   │ Input:                                         │  - file       │
│               │  > /ask ...                                    │  - diff       │
│ Shortcuts     │  > /tool ...                                   │  - logs       │
│  ? help       │  > /open path                                  │               │
│  ⌘K commands  │                                                │ Status        │
│               │ Approvals (when pending)                       │  ✓ clean tree │
│               │  [A] approve  [D] deny  [V] view args          │  ⏱ last run   │
├───────────────┴───────────────────────────────────────────────┴───────────────┤
│ F1 Help  F2 Sessions  F3 Tasks  F4 Files  F5 Diff  F6 Logs  ⌘K Command Palette │
└───────────────────────────────────────────────────────────────────────────────┘
```

### Mermaid: Layout Grid

```mermaid
flowchart LR
  A[Top Bar<br/>App / Session / Model / Status]:::top
  B[Sidebar<br/>Sessions / Tasks / Shortcuts]:::side
  C[Chat Pane<br/>Messages / Tool Calls / Input]:::main
  D[Workspace Pane<br/>Files / Preview Tabs / Status]:::work

  A --> B
  A --> C
  A --> D
  B --- C --- D

  classDef top fill:#111,stroke:#555,color:#fff
  classDef side fill:#151515,stroke:#555,color:#fff
  classDef main fill:#101820,stroke:#555,color:#fff
  classDef work fill:#0f1a14,stroke:#555,color:#fff
```

---

## Panes & Components

### 1) Top Bar

* App title
* Active session name
* Active model / profile
* Connection state (local/remote)
* Recording / tracing indicator (optional)

**States**

* `Connected | Disconnected | Reconnecting`
* `Idle | Running tool | Awaiting approval | Streaming`

---

### 2) Sidebar (Sessions + Tasks)

**Sections**

* Sessions list (recent, pinned, archived)
* Tasks list (agent subtasks / TODOs)
* Quick nav shortcuts
* Context badges (branch, env, container, cluster)

**Keyboard**

* `↑/↓` navigate
* `Enter` open
* `n` new session
* `p` pin
* `a` archive
* `/` filter

---

### 3) Chat / Orchestration Pane

**Message types**

* User message
* Assistant message
* Tool call (collapsed/expandable)
* Tool result (stdout/stderr + artifacts)
* Approval prompt card (when tool is gated)

**Input**

* Single-line default, expands to multiline
* Slash commands: `/ask /tool /open /diff /run /reset`

---

### 4) Workspace Pane

**Subpanes**

* File tree
* Preview tabs:

  * File viewer (read-only / edit mode optional)
  * Diff viewer
  * Logs viewer (tool execution logs)
  * “Artifacts” (generated files, reports, patches)

---

## Mermaid: Navigation Map (Global)

```mermaid
stateDiagram-v2
  [*] --> Chat

  Chat --> Sessions: F2
  Chat --> Tasks: F3
  Chat --> Files: F4
  Chat --> Diff: F5
  Chat --> Logs: F6
  Chat --> Palette: Cmd+K

  Sessions --> Chat: Esc / Enter session
  Tasks --> Chat: Esc / Enter task
  Files --> Chat: Esc
  Diff --> Chat: Esc
  Logs --> Chat: Esc
  Palette --> Chat: Esc / Execute command
```

---

## Mermaid: Tool Execution & Approvals

```mermaid
sequenceDiagram
  participant U as User
  participant TUI as TUI
  participant A as Agent
  participant TOOLS as Tools/Runtime

  U->>TUI: Prompt / command
  TUI->>A: send(input)
  A-->>TUI: tool_call प्रस्ताव (name, args, risk)
  alt requires approval
    TUI-->>U: Approval Card (A approve / D deny / V view)
    U->>TUI: Approve
    TUI->>TOOLS: execute(tool, args)
    TOOLS-->>TUI: result (stdout, artifacts)
    TUI->>A: tool_result
    A-->>TUI: updated response + next actions
  else auto-approved
    TUI->>TOOLS: execute(tool, args)
    TOOLS-->>TUI: result
    TUI->>A: tool_result
    A-->>TUI: updated response
  end
```

---

## “OpenCode Style” Screens (Secondary Views)

### A) Command Palette (⌘K)

```
┌───────────────────────────────────────────────┐
│ Command Palette                                │
├───────────────────────────────────────────────┤
│ > open session                                 │
│   run tool                                      │
│   search files                                  │
│   show diff                                     │
│   toggle approvals                              │
│   change model                                  │
│   export transcript                             │
├───────────────────────────────────────────────┤
│ Enter: run   Esc: close   ↑↓: select            │
└───────────────────────────────────────────────┘
```

### B) Approval Card (inline in chat)

```
┌─ Approval Required ────────────────────────────┐
│ Tool: shell.exec                                │
│ Risk: writes files / runs commands              │
│ Args:                                            │
│   cmd: ["pytest", "-q"]                          │
│   cwd: server/                                   │
│                                                  │
│ [A] Approve   [D] Deny   [V] View Full JSON      │
└──────────────────────────────────────────────────┘
```

### C) Diff View (Workspace tab)

```
┌─ Diff: src/main.py ─────────────────────────────┐
│ @@ -12,7 +12,15 @@                               │
│ -def run():                                      │
│ +def run(mode="tui"):                            │
│ +    ...                                         │
│                                                  │
│ [N] next hunk  [P] prev  [A] apply patch  [Q] quit│
└──────────────────────────────────────────────────┘
```

---

## Keybindings (Suggested)

| Area     | Keys                          | Action                |
| -------- | ----------------------------- | --------------------- |
| Global   | `⌘K`                          | Command palette       |
| Global   | `Esc`                         | Back / close modal    |
| Tabs     | `Ctrl+Tab` / `Ctrl+Shift+Tab` | Cycle workspace tabs  |
| Sessions | `F2`                          | Focus sessions        |
| Tasks    | `F3`                          | Focus tasks           |
| Files    | `F4`                          | Focus files           |
| Diff     | `F5`                          | Open diff             |
| Logs     | `F6`                          | Open logs             |
| Chat     | `Enter`                       | Send                  |
| Chat     | `Shift+Enter`                 | New line              |
| Approval | `A/D/V`                       | Approve / Deny / View |

---

## Minimal Data Model (Conceptual)

```mermaid
classDiagram
  class Session {
    +id: str
    +name: str
    +created_at: datetime
    +model_profile: str
    +messages: Message[]
    +workspace_root: path
    +approvals_mode: enum
  }

  class Message {
    +id: str
    +role: user|assistant|tool
    +content: str
    +timestamp: datetime
  }

  class ToolCall {
    +id: str
    +name: str
    +args: json
    +status: pending|running|done|failed|denied
    +requires_approval: bool
  }

  class Artifact {
    +path: str
    +type: file|patch|report
    +created_by: tool
  }

  Session "1" o-- "*" Message
  Session "1" o-- "*" ToolCall
  ToolCall "1" o-- "*" Artifact
```

---

## Optional: “3-Pane Mode” Toggle

* **Focus Chat** (hide sidebar/workspace)
* **Focus Workspace** (diff + file editing)
* **Focus Sessions/Tasks** (triage)

```mermaid
stateDiagram-v2
  [*] --> ThreePane
  ThreePane --> FocusChat: Ctrl+1
  ThreePane --> FocusWorkspace: Ctrl+2
  ThreePane --> FocusSidebar: Ctrl+3
  FocusChat --> ThreePane: Ctrl+0
  FocusWorkspace --> ThreePane: Ctrl+0
  FocusSidebar --> ThreePane: Ctrl+0
```

---

If you want, I can also generate:

* a **component tree** that matches a specific TUI framework (Textual, Bubble Tea, Ratatui),
* an **event loop + state machine** sketch,
* and a **screen-by-screen spec** (what renders where, what state each view needs).
