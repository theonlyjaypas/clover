# Clover Restaurant - AI-Powered Ordering System

A modern restaurant ordering platform powered by Claude AI with tool-use capabilities. Clover provides an intelligent chatbot interface for customers to browse the menu, get recommendations, and place orders seamlessly.

## Features

- **AI Chatbot Interface** - Claude-powered conversational ordering assistant ("Priya")
- **Menu Management** - SQLite database with categories, items, prices, and descriptions
- **Order Management** - Track and manage customer orders with status updates
- **Admin Dashboard** - Secure admin panel to view, update, and manage orders
- **Tool-Use Integration** - Claude agent autonomously calls tools to search menu, manage cart, and place orders
- **Authentication** - Session-based authentication for admin access
- **REST API** - Full-featured API for menu, chat, and order operations

## Tech Stack

- **Backend**: FastAPI 0.135.2
- **AI Model**: Anthropic Claude Haiku 4.5
- **Database**: SQLite
- **Server**: Uvicorn
- **Validation**: Pydantic 2.13.4
- **Environment**: Python 3.8+

## Project Structure

```
.
├── server.py                 # Uvicorn entry point
├── requirements.txt          # Python dependencies
├── .env                      # Environment variables (DO NOT COMMIT)
├── .gitignore               # Git ignore rules
├── src/
│   ├── servers/
│   │   ├── server.py        # Main FastAPI application & Claude integration
│   │   ├── chatbot_server.py
│   │   └── orders_server.py
│   └── database/
│       ├── db.py            # Database connection utilities
│       ├── setup_db.py      # Database initialization
│       └── menu.db          # SQLite database (git-ignored)
├── static/
│   ├── templates/
│   │   ├── chatbot.html     # Customer chatbot interface
│   │   ├── login.html       # Admin login page
│   │   └── orders.html      # Order management dashboard
│   └── css/
├── docs/
│   └── DEPLOYMENT.md        # Deployment documentation
├── logs/                    # Application logs (git-ignored)
└── .github/                 # GitHub workflows and config
```

## Installation

### Prerequisites

- Python 3.8 or higher
- pip or poetry for package management
- API keys for Anthropic Claude (required)

### Setup

1. **Clone the repository**
   ```bash
   git clone https://github.com/yourusername/clover-restaurant.git
   cd clover-restaurant
   ```

2. **Create virtual environment**
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

4. **Configure environment variables**
   ```bash
   cp .env.example .env
   ```
   
   Edit `.env` with your configuration:
   ```env
   ANTHROPIC_API_KEY=sk-ant-xxxxxxxxxxxx
   OPENAI_API_KEY=sk-proj-xxxxxxxxxxxx
   ADMIN_USERNAME=yourusername
   ADMIN_PASSWORD=yoursecurepassword
   DB_PATH=./src/database/menu.db
   ```

5. **Initialize database** (if needed)
   ```bash
   python src/database/setup_db.py
   ```

## Running the Application

### Development Server

```bash
uvicorn server:app --reload --host 0.0.0.0 --port 8000
```

The application will be available at `http://localhost:8000`

- **Chatbot Interface**: http://localhost:8000/
- **Admin Login**: http://localhost:8000/admin
- **API Docs**: http://localhost:8000/docs (Swagger UI)
- **API Redoc**: http://localhost:8000/redoc

### Production Server

```bash
uvicorn server:app --host 0.0.0.0 --port 8000 --workers 4
```

## API Endpoints

### Chat

**POST** `/api/chat`

Send a message to Claude for conversational ordering.

Request:
```json
{
  "messages": [
    {"role": "user", "content": "Show me vegetarian dishes"}
  ]
}
```

Response:
```json
{
  "response": "Here are our vegetarian options...",
  "model": "claude-haiku-4-5-20251001",
  "cart_items": []
}
```

### Menu

**GET** `/api/menu`

Retrieve the complete menu with all categories and items.

Response:
```json
{
  "restaurant": "Clover",
  "menu": [
    {
      "category": "Soups",
      "items": [
        {
          "name": "Tomato Soup",
          "price": "$4.99",
          "description": "Fresh tomato with spices"
        }
      ]
    }
  ]
}
```

### Orders

**POST** `/api/orders`

Create a new order.

Request:
```json
{
  "items": [
    {"name": "Butter Chicken", "price": "$12.99", "qty": 1}
  ],
  "customer_name": "John Doe",
  "phone_number": "555-1234",
  "pickup_datetime": "2026-07-20 18:30"
}
```

**GET** `/api/orders` (Requires authentication)

List all orders. Requires admin authentication cookie.

**PATCH** `/api/orders/{order_id}/status` (Requires authentication)

Update order status.

Request:
```json
{
  "status": "In Progress"
}
```

Valid statuses: `Pending`, `In Progress`, `Ready`, `Completed`, `Cancelled`

**DELETE** `/api/orders/{order_id}` (Requires authentication)

Delete an order.

### Authentication

**POST** `/api/login`

Login to admin dashboard.

Request:
```json
{
  "username": "yourusername",
  "password": "yoursecurepassword"
}
```

**POST** `/api/logout`

Logout from admin dashboard.

## Claude AI Integration

The chatbot is powered by Claude with tool-use capabilities. The agent has access to:

- **search_menu** - Search menu items by keyword
- **get_menu_categories** - List all categories
- **get_items_by_category** - Get items in a category
- **add_to_cart** - Add items to customer's cart
- **place_order** - Submit order to database

The system prompt defines "Priya" as a warm, knowledgeable waiter with personality and expertise in Indian cuisine.

## Environment Variables

| Variable | Description | Example |
|----------|-------------|---------|
| `ANTHROPIC_API_KEY` | Claude API key (required) | `sk-ant-...` |
| `OPENAI_API_KEY` | OpenAI API key (optional) | `sk-proj-...` |
| `ADMIN_USERNAME` | Admin username for dashboard | `jaypas` |
| `ADMIN_PASSWORD` | Admin password for dashboard | `R3load24` |
| `DB_PATH` | Path to SQLite database | `./src/database/menu.db` |

## Security Considerations

1. **Never commit `.env` files** - These contain sensitive API keys and credentials
2. **Use environment variables** in production instead of hardcoded values
3. **Password hashing** - Admin passwords are hashed with PBKDF2-SHA256 (260k iterations)
4. **Session management** - Sessions expire after 8 hours
5. **HTTP-only cookies** - Session cookies are HTTP-only and SameSite=Lax
6. **CORS** - Configure appropriately for production deployments

## Database Schema

### orders table

```sql
CREATE TABLE orders (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at TEXT DEFAULT (datetime('now')),
  items_json TEXT NOT NULL,
  total REAL NOT NULL,
  customer_name TEXT,
  phone_number TEXT,
  pickup_datetime TEXT,
  status TEXT DEFAULT 'Pending',
  source TEXT DEFAULT 'chatbot'
);
```

### categories table
Menu categories (e.g., Soups, Breads, Desserts, Biryani & Rice)

### menu_items table
Individual menu items with name, price, description, and category_id

## Development

### Running Tests

```bash
pytest tests/ -v
```

### Code Style

- Follow PEP 8
- Use type hints where possible
- Document complex functions with docstrings

### Adding New Tools

1. Add tool definition to `TOOLS` list in `server.py`
2. Implement handler function (e.g., `tool_function_name`)
3. Add entry to `_HANDLERS` dictionary
4. Update system prompt if needed

## Deployment

See [DEPLOYMENT.md](docs/DEPLOYMENT.md) for detailed deployment instructions including:

- Docker containerization
- Cloud deployment (AWS, GCP, Azure)
- Environment setup
- SSL/TLS configuration
- Load balancing
- Monitoring and logging

## Troubleshooting

### API Keys Not Found
Ensure `.env` file exists in project root with valid `ANTHROPIC_API_KEY`.

### Database Connection Error
Check that `DB_PATH` is correct and the database file has read/write permissions.

### Chat Endpoint Returning 500 Error
Check logs for agentic loop issues. The safety cap is set to 10 tool-use iterations.

### Admin Login Not Working
Verify `ADMIN_USERNAME` and `ADMIN_PASSWORD` environment variables match your credentials.

## Contributing

1. Create a feature branch (`git checkout -b feature/amazing-feature`)
2. Commit changes (`git commit -m 'Add amazing feature'`)
3. Push to branch (`git push origin feature/amazing-feature`)
4. Open a Pull Request

## License

This project is licensed under the MIT License - see LICENSE file for details.

## Support

For issues, questions, or contributions, please open an issue on GitHub or contact the development team.

## Changelog

### v1.0.0 (2026-07-20)
- Initial release
- Core chatbot functionality
- Admin order management
- Menu database integration
- Claude AI tool-use integration
