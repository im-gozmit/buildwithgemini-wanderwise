# My agent: Ghumo Duniya - AI Travel Concierge & Road Trip Companion
One-liner: An agent-first travel concierge that plans road trips with 1-hour detours, searches curated spots in Firestore, calculates INR budgets, and generates visual postcards with A2UI.

Tool coverage:
- Memory: Remembers traveler preferences across sessions (budget tier, travel pace, dietary restrictions, party size/style, and past visited destinations) via Vertex AI Memory Bank.
- Tools: Suggests 1-hour road trip detours via OpenStreetMap/OSRM, searches curated travel spots in Firestore, calculates itemized INR (₹) budgets, and checks destination weather via Open-Meteo.
- Catalog/UI: Multi-day itineraries and destination spot highlights rendered with interactive cards via A2UI.
- Image gen: Generates scenic destination postcards using Gemini image generation and uploads to Google Cloud Storage.
- Sandbox: Agent Engine sandbox code execution for budget calculations.

