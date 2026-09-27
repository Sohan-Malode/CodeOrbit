# CodeOrbit

AI-powered code repository architecture visualizer and analyzer.

CodeOrbit analyzes software repositories and transforms their code structure into interactive architecture and dependency views. It helps developers understand how files, modules, functions, and dependencies are connected.

## Features

- 📦 Analyze local ZIP repositories
- 🔗 Analyze public GitHub repositories
- 🏗️ Interactive Architecture Graph
- 🔗 Dependency Graph
- 🔄 Mermaid Flow Diagram
- 📁 File Explorer
- 📄 File-level code and dependency details
- 🤖 AI-powered file explanations using Ollama
- 📊 Project insights and statistics
- 💾 Export analysis results
- 🎨 Modern dark-themed interface

## Tech Stack

### Frontend
- HTML
- CSS
- JavaScript

### Backend
- Python
- Flask
- AST-based Python code analysis

### AI
- Ollama
- Qwen 2.5 Coder 3B

## Project Structure

```text
CodeOrbit/
├── assets/
│   ├── languages/
│   └── logo/
├── backend/
│   ├── analyzer.py
│   ├── app.py
│   ├── requirements.txt
│   └── sample_repo/
├── index.html
├── script.js
├── styles.css
├── .gitignore
└── README.md