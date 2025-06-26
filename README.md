# Conversational AI Agentic Bot with Workflows

A private AI-powered customer service and automation system, a premium travel accessories retailer.
Workflow/Use cases details metioned in Workflows.docx

## 🚀 Project Overview

NasherMiles AI is a sophisticated customer service and automation platform that combines AI capabilities with various business systems to provide seamless customer interactions and efficient operations. The system is designed to handle customer inquiries, order management, social media automation, and more.

## 🔐 Security Notice

This is a private project. Please ensure:
- Never share credentials or sensitive information
- Keep `.env` and `credentials.json` secure
- Follow security protocols strictly

## 📁 Project Structure

```
.
├── AI_Agent.py          # Main AI agent implementation
├── AI_tools.py          # AI utility functions and tools
├── insta_api.py         # Instagram API integration
├── mongodb_handler.py   # MongoDB database operations
├── nasher_miles_app.py  # Main application entry point
├── scheduler.py         # Task scheduling system
├── spreadsheet.py       # Spreadsheet operations
├── static/              # Static assets
├── templates/           # Template files
├── logs/               # Application logs
└── requirements.txt    # Project dependencies
```

## 🛠️ Setup Instructions

1. **Install Dependencies**
```bash
pip install -r requirements.txt
```

2. **Configure Environment**
- Copy `.env.example` to `.env`
- Fill in required credentials and API keys
- Keep `.env` secure and never commit it

3. **Run the Application**
```bash
python nasher_miles_app.py
```

## 🔑 Configuration

The application uses environment variables for configuration. Key variables include:
- API keys and credentials
- Database connection strings
- Scheduling configurations
- Social media credentials

## 🚀 Key Features

- **AI-Powered Customer Service**
  - Natural language processing
  - Product recommendations
  - Discount inquiries handling
  - Influencer collaboration management

- **Order Management**
  - Shopify integration
  - Order status tracking
  - Customer information management

- **Social Media Automation**
  - Instagram API integration
  - Content scheduling
  - Interaction management

- **Database Integration**
  - MongoDB for data storage
  - Error logging and monitoring
  - Data backup system

## 🛡️ Security

- All sensitive information is stored securely
- Environment variables are used for credentials
- Regular security audits are performed
- Access controls are implemented

## 📝 Error Handling

The application includes comprehensive error logging:
- MongoDB errors: `mongodb_handler_errors.log`
- Instagram API errors: `insta_api_errors.log`
- General errors: `error_logger.py`
