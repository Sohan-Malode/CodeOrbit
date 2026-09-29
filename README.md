# CodeOrbit

### Turn Code into Clarity

CodeOrbit is an AI-powered code repository architecture visualizer and analyzer.

It analyzes an existing backend repository, extracts its structure and dependencies, dynamically generates Mermaid.js architecture code, and transforms the results into an interactive visual representation of the system.

Developers can explore files, dependencies, architecture relationships, project statistics, and use AI to understand the role of individual components.

---

## Problem Statement

### Code-to-Diagram Architecture Visualizer

Onboarding developers to an existing codebase can be difficult because system architecture documentation is often incomplete or outdated.

CodeOrbit addresses this by automatically analyzing a provided backend repository and converting its code structure and dependencies into a visual architecture representation.

The system can:

- Read a provided repository
- Detect source files and project structure
- Analyze dependencies and relationships
- Generate Mermaid.js architecture code automatically
- Render the architecture dynamically
- Allow developers to explore individual files
- Provide AI-powered explanations of selected components

---

## Features

- 📦 Analyze repositories from ZIP files
- 🔗 Analyze public GitHub repositories
- 🧪 Analyze the included sample repository
- 🏗️ Interactive architecture visualization
- 🔀 Dependency relationship visualization
- 🌐 Automatic Mermaid.js architecture generation
- 📈 Dynamic Mermaid architecture rendering
- 📁 File Explorer
- 🔎 Source code preview
- 📄 File-level dependency information
- 🤖 AI-powered architectural explanations
- 📊 Project statistics and architecture insights
- 💾 Export analysis results as JSON
- 🌙 Dark and light themes
- 💻 Programming language detection
- 🧩 Folder and module organization
- 🔗 File-to-file dependency mapping

---

## How It Works

CodeOrbit converts a repository into an interactive architecture model through several stages.

```text
Repository
    │
    ├── ZIP Upload
    │
    └── Public GitHub Repository
            │
            ▼
     Repository Analyzer
            │
            ▼
      Source File Detection
            │
            ▼
    Dependency Extraction
            │
            ▼
     Architecture Graph
            │
       ┌────┴────┐
       ▼         ▼
     Nodes      Edges
       │         │
       └────┬────┘
            ▼
   Mermaid.js Generation
            │
            ▼
  Dynamic Architecture View
            │
            ▼
    Select a Component
            │
            ▼
       Gemini AI
            │
            ▼
 Architectural Explanation