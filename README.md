# 📬 Outlook → Notion → LLM → Auto-Reply Assistant

An end-to-end **AI-powered executive assistant** that synchronizes Outlook emails into Notion, enables human-in-the-loop instructions, generates professional replies using LLMs, and sends responses back through Microsoft Outlook automatically.

This project demonstrates **real-world automation**, **API orchestration**, and **human–AI collaboration** for high-stakes communication workflows.

---

## 🚀 Project Overview

Senior leaders (e.g., directors, executives, professors) receive a high volume of emails but cannot manually respond to all of them efficiently.

This system solves that problem by:

1. Reading emails from Outlook  
2. Syncing them into Notion as structured records  
3. Allowing a human to add reply instructions  
4. Generating professional replies using an LLM  
5. Sending replies back through Outlook automatically  
6. Updating Notion with sent status and response  

The human stays **in control**, while AI handles drafting and execution.

---

## 🧠 Key Features

- 🔐 Secure Microsoft Graph authentication (OAuth 2.0 / MSAL)
- 📥 Read & parse Outlook emails
- 🗂️ Sync emails to a Notion database
- ✍️ Human-written reply instructions (human-in-the-loop)
- 🤖 LLM-generated professional email replies (Groq / GPT-OSS)
- 📤 Automated Outlook replies via Microsoft Graph API
- ✅ Notion status updates after successful send
- 🔄 Safe, idempotent execution (no auto-send without instruction)

---

## 🏗️ System Architecture
**<img width="1002" height="567" alt="Gmail Sync with Notion" src="https://github.com/user-attachments/assets/8da9392d-bfcc-4f0f-8a81-269819bee166" />

## 🧰 Technologies Used

### Backend & Automation
- **Python 3.10+
- **Requests**
- **python-dotenv**

### Microsoft Integration
- **Microsoft Graph API**
- **MSAL (OAuth 2.0 Device Code Flow)**  
- Required permissions:
  - `User.Read`
  - `Mail.Read`
  - `Mail.ReadWrite`
  - `Mail.Send`

### AI / LLM
- **OPENAI: gpt-4o-mini**
- **GPT-OSS-120B**
- Prompt-engineered for executive-level email responses

### Workspace & Human-in-the-Loop
- **Notion API**
- Structured Notion database for review and instruction

### 🌱 Future Work

- 🧠 Email classification (urgent, FYI, spam)
- 🖊️ Signature injection and personalization
- 👥 Multi-account & shared mailbox support
- 📊 Analytics dashboard (email volume, response times)
- 🧪 LLM A/B testing
- 📨 Calendar Integration
- 🔐 Role-based permissions in Notion
- Fetching Agendas to create action items and displaying on the dashbaord
- Parsing Zoom Meeting Transcripts to create to-do lists

