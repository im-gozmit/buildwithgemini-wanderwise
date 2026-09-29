# ruff: noqa
# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import datetime
import json
import os
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()

from a2ui.basic_catalog.provider import BasicCatalog
from a2ui.schema.manager import A2uiSchemaManager
from google.adk.agents import Agent
from google.adk.agents.callback_context import CallbackContext
from google.adk.apps import App
from google.adk.code_executors.agent_engine_sandbox_code_executor import AgentEngineSandboxCodeExecutor
from google.adk.models import Gemini
from google.adk.tools.load_memory_tool import LoadMemoryTool
from google.adk.tools.tool_context import ToolContext
from google.cloud import firestore, storage
from google import genai
from google.genai import types

from app.a2ui_utils import a2ui_callback

MODEL = "gemini-2.5-flash"

# Hardcoded project ID: on Agent Platform, GOOGLE_CLOUD_PROJECT/auth.default() return
# the project number, which causes Firestore (default) database lookups to fail (404 NotFound).
FIRESTORE_PROJECT_ID = "qwiklabs-gcp-04-7b122a8e9728"
GCS_BUCKET_NAME = "wanderwise-travel-media-7b122a8e"

db = firestore.Client(project=FIRESTORE_PROJECT_ID)
storage_client = storage.Client(project=FIRESTORE_PROJECT_ID)
genai_client = genai.Client(vertexai=True, project=FIRESTORE_PROJECT_ID, location="global")


def search_travel_spots(
    city: Optional[str] = None,
    category: Optional[str] = None,
    budget_tier: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Search the travel catalog in Firestore for spots, attractions, and activities.

    Args:
        city: Optional filter by city name (e.g. "Kyoto", "Paris", "San Francisco").
        category: Optional category filter (e.g. "sightseeing", "food_and_drink", "museums").
        budget_tier: Optional tier filter ("budget", "moderate", "luxury").

    Returns:
        A list of matching travel spot documents with details like description, duration, cost, and rating.
    """
    collection_ref = db.collection("travel_spots")
    docs = collection_ref.stream()

    results = []
    for doc in docs:
        data = doc.to_dict()
        if city and city.strip().lower() not in data.get("city", "").lower():
            continue
        if category and category.strip().lower() not in data.get("category", "").lower():
            continue
        if budget_tier and budget_tier.strip().lower() != data.get("budget_tier", "").lower():
            continue
        results.append(data)

    return results


def save_travel_spot(
    spot_id: str,
    name: str,
    city: str,
    country: str,
    category: str,
    budget_tier: str,
    description: str,
    duration_hours: float,
    cost_inr: float = 0.0,
    cost_usd: Optional[float] = None,
    rating: float = 4.5,
) -> Dict[str, Any]:
    """Add or update a travel spot in the Firestore catalog.

    Args:
        spot_id: Unique slug/id for the spot (e.g. "jaipur_amber_fort", "kyoto_arashiyama").
        name: The title/name of the spot or activity.
        city: City where the spot is located.
        country: Country where the spot is located.
        category: Category (e.g. "sightseeing", "food_and_drink", "museums", "outdoors").
        budget_tier: Budget tier ("budget", "moderate", "luxury").
        description: Description of the experience or attraction.
        duration_hours: Estimated time to spend in hours.
        cost_inr: Estimated cost per person in Indian Rupees (INR - ₹).
        cost_usd: Optional cost in USD (converted to INR at 85 if cost_inr not provided).
        rating: Rating out of 5.0 (default: 4.5).

    Returns:
        A dictionary confirming the spot was saved successfully.
    """
    final_inr = float(cost_inr)
    if final_inr <= 0 and cost_usd is not None:
        final_inr = round(float(cost_usd) * 85.0, 2)
    data = {
        "id": spot_id,
        "name": name,
        "city": city,
        "country": country,
        "category": category,
        "budget_tier": budget_tier,
        "description": description,
        "duration_hours": float(duration_hours),
        "cost_inr": final_inr,
        "currency": "INR",
        "rating": float(rating),
    }
    db.collection("travel_spots").document(spot_id).set(data)
    return {"status": "success", "message": f"Saved spot '{name}' to Firestore.", "spot": data}


def save_user_itinerary(
    itinerary_id: str,
    trip_title: str,
    destination: str,
    days: int,
    notes: str = "",
) -> Dict[str, Any]:
    """Save or update a planned traveler itinerary in Firestore.

    Args:
        itinerary_id: Unique identifier for this itinerary (e.g. "kyoto_spring_trip_2026").
        trip_title: Title of the trip.
        destination: Destination city or country.
        days: Number of days planned.
        notes: General notes, highlights, or schedule details.

    Returns:
        A dictionary confirming the itinerary was stored.
    """
    data = {
        "itinerary_id": itinerary_id,
        "trip_title": trip_title,
        "destination": destination,
        "days": int(days),
        "notes": notes,
        "updated_at": datetime.datetime.now(ZoneInfo("UTC")).isoformat(),
    }
    db.collection("itineraries").document(itinerary_id).set(data)
    return {"status": "success", "message": f"Saved itinerary '{trip_title}' to Firestore.", "itinerary": data}


def get_user_itinerary(itinerary_id: str) -> Dict[str, Any]:
    """Retrieve a previously saved travel itinerary by ID from Firestore.

    Args:
        itinerary_id: The identifier of the itinerary to retrieve.

    Returns:
        The itinerary document or a not-found message.
    """
    doc = db.collection("itineraries").document(itinerary_id).get()
    if not doc.exists:
        return {"status": "not_found", "message": f"Itinerary with id '{itinerary_id}' was not found."}
    return {"status": "success", "itinerary": doc.to_dict()}


def estimate_trip_budget(
    city: str,
    days: int,
    budget_tier: str = "moderate",
    travelers: int = 1,
) -> Dict[str, Any]:
    """Estimate the total trip budget broken down by accommodation, meals, transit, and activities in Indian Rupees (INR - ₹).

    Args:
        city: Destination city name (e.g. "Kyoto", "Paris", "Jaipur", "Goa").
        days: Trip duration in days.
        budget_tier: Travel tier ("budget", "moderate", "luxury"). Default is "moderate".
        travelers: Number of travelers. Default is 1.

    Returns:
        Itemized budget breakdown and total estimated cost in Indian Rupees (INR - ₹).
    """
    days = max(1, int(days))
    travelers = max(1, int(travelers))
    tier = (budget_tier or "moderate").strip().lower()

    # Base daily rates per person based on tier in INR (₹)
    tier_rates_inr = {
        "budget": {"hotel_per_night": 2500.0, "daily_food": 1200.0, "daily_transit": 500.0, "daily_activities": 800.0},
        "moderate": {"hotel_per_night": 6500.0, "daily_food": 2800.0, "daily_transit": 1500.0, "daily_activities": 2000.0},
        "luxury": {"hotel_per_night": 18000.0, "daily_food": 7500.0, "daily_transit": 4500.0, "daily_activities": 6000.0},
    }
    rates = tier_rates_inr.get(tier, tier_rates_inr["moderate"])

    # Number of hotel rooms needed (assume 2 persons per room)
    rooms = (travelers + 1) // 2
    nights = max(1, days - 1) if days > 1 else 1

    lodging_cost = round(rooms * nights * rates["hotel_per_night"], 2)
    food_cost = round(travelers * days * rates["daily_food"], 2)
    transit_cost = round(travelers * days * rates["daily_transit"], 2)
    activities_cost = round(travelers * days * rates["daily_activities"], 2)
    total_cost = round(lodging_cost + food_cost + transit_cost + activities_cost, 2)

    return {
        "city": city,
        "days": days,
        "travelers": travelers,
        "budget_tier": tier,
        "currency": "INR",
        "currency_symbol": "₹",
        "breakdown_inr": {
            "lodging": lodging_cost,
            "meals": food_cost,
            "local_transit": transit_cost,
            "activities": activities_cost,
        },
        "estimated_total_inr": total_cost,
        "estimated_per_person_inr": round(total_cost / travelers, 2),
        "formatted_total": f"₹{total_cost:,.2f}",
        "formatted_per_person": f"₹{round(total_cost / travelers, 2):,.2f}",
    }


def get_destination_weather(city: str, forecast_days: int = 3) -> Dict[str, Any]:
    """Fetch real-time weather and a multi-day forecast for any travel destination using the Open-Meteo public API.

    Args:
        city: The destination city name (e.g. "Kyoto", "Paris", "San Francisco").
        forecast_days: Number of days to forecast (1 to 7, default: 3).

    Returns:
        Current temperature, general condition, and daily high/low forecast with rain probability.
    """
    days = max(1, min(7, int(forecast_days)))
    encoded_city = urllib.parse.quote(city.strip())
    geo_url = f"https://geocoding-api.open-meteo.com/v1/search?name={encoded_city}&count=1&language=en&format=json"

    req = urllib.request.Request(geo_url, headers={"User-Agent": "WanderWiseAgent/1.0"})
    with urllib.request.urlopen(req, timeout=8) as resp:
        geo_data = json.loads(resp.read().decode())

    results = geo_data.get("results")
    if not results:
        return {"status": "error", "message": f"Could not locate destination city '{city}'."}

    loc = results[0]
    lat, lon = loc["latitude"], loc["longitude"]
    resolved_name = f"{loc.get('name')}, {loc.get('country', '')}".strip(", ")

    # Weather codes mapping
    wmo_interpretations = {
        0: "Clear sky",
        1: "Mainly clear",
        2: "Partly cloudy",
        3: "Overcast",
        45: "Fog",
        51: "Light drizzle",
        53: "Moderate drizzle",
        61: "Slight rain",
        63: "Moderate rain",
        65: "Heavy rain",
        71: "Slight snow fall",
        80: "Rain showers",
        95: "Thunderstorm",
    }

    forecast_url = (
        f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}"
        f"&current=temperature_2m,weather_code"
        f"&daily=weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max"
        f"&timezone=auto&forecast_days={days}"
    )

    req_f = urllib.request.Request(forecast_url, headers={"User-Agent": "WanderWiseAgent/1.0"})
    with urllib.request.urlopen(req_f, timeout=8) as resp_f:
        forecast_data = json.loads(resp_f.read().decode())

    current = forecast_data.get("current", {})
    daily = forecast_data.get("daily", {})

    daily_summary = []
    dates = daily.get("time", [])
    max_temps = daily.get("temperature_2m_max", [])
    min_temps = daily.get("temperature_2m_min", [])
    precip_probs = daily.get("precipitation_probability_max", [])
    codes = daily.get("weather_code", [])

    for i in range(len(dates)):
        code = codes[i] if i < len(codes) else 0
        daily_summary.append({
            "date": dates[i],
            "condition": wmo_interpretations.get(code, "Variable"),
            "high_c": max_temps[i] if i < len(max_temps) else None,
            "low_c": min_temps[i] if i < len(min_temps) else None,
            "rain_chance_pct": precip_probs[i] if i < len(precip_probs) else 0,
        })

    curr_code = current.get("weather_code", 0)
    return {
        "status": "success",
        "destination": resolved_name,
        "current_temperature_c": current.get("temperature_2m"),
        "current_condition": wmo_interpretations.get(curr_code, "Variable"),
        "daily_forecast": daily_summary,
    }


def geocode_address(address: str) -> Dict[str, Any]:
    """Convert a street address or location name into geographic latitude and longitude coordinates using Google Maps Geocoding API.

    Args:
        address: The address or place to geocode (e.g. "1600 Amphitheatre Pkwy, Mountain View, CA" or "Eiffel Tower, Paris").

    Returns:
        A dictionary containing formatted address, coordinates (lat, lng), and place_id.
    """
    api_key = os.getenv("GOOGLE_MAPS_API_KEY", "").strip()
    if not api_key or api_key == "PASTE_KEY_HERE":
        # Free OpenStreetMap Nominatim geocoding fallback (no API key required)
        encoded_addr = urllib.parse.quote(address.strip())
        url = f"https://nominatim.openstreetmap.org/search?q={encoded_addr}&format=json&limit=1"
        req = urllib.request.Request(url, headers={"User-Agent": "WanderWiseTripAssistant/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read().decode())
            if not data:
                return {"status": "error", "message": f"Could not find coordinates for '{address}'"}
            top = data[0]
            lat = float(top["lat"])
            lng = float(top["lon"])
            return {
                "status": "success",
                "name": address,
                "formatted_address": top.get("display_name"),
                "location": {"lat": lat, "lng": lng},
                "place_id": str(top.get("place_id", "")),
                "source": "OpenStreetMap",
            }
        except Exception as e:
            return {"status": "error", "message": f"OSM Geocoding request failed: {str(e)}"}

    encoded_addr = urllib.parse.quote(address.strip())
    url = f"https://maps.googleapis.com/maps/api/geocode/json?address={encoded_addr}&key={api_key}"

    req = urllib.request.Request(url, headers={"User-Agent": "WanderWiseAgent/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
    except Exception as e:
        return {"status": "error", "message": f"Geocoding request failed: {str(e)}"}

    if data.get("status") != "OK" or not data.get("results"):
        return {
            "status": "error",
            "message": f"Geocoding error: {data.get('status')} - {data.get('error_message', 'No results found.')}",
        }

    top = data["results"][0]
    return {
        "status": "success",
        "name": address,
        "formatted_address": top.get("formatted_address"),
        "location": top.get("geometry", {}).get("location"),
        "place_id": top.get("place_id"),
        "source": "GoogleMaps",
    }


def find_nearby_places(
    latitude: float,
    longitude: float,
    place_type: str,
    radius_meters: float = 1000.0,
    max_results: int = 5,
) -> Dict[str, Any]:
    """Find nearby places of a specific type (e.g. restaurant, museum, cafe, tourist_attraction) using Places API (New).

    Args:
        latitude: Center latitude coordinate (e.g. 35.6585).
        longitude: Center longitude coordinate (e.g. 139.7454).
        place_type: Type of place to search (e.g. "restaurant", "cafe", "museum", "tourist_attraction", "lodging").
        radius_meters: Search radius in meters (max 50000, default: 1000.0).
        max_results: Maximum number of places to return (1-20, default: 5).

    Returns:
        List of matching places with display name, formatted address, location coordinates, and place types.
    """
    api_key = os.getenv("GOOGLE_MAPS_API_KEY", "").strip()
    if not api_key or api_key == "PASTE_KEY_HERE":
        return {
            "status": "error",
            "message": "GOOGLE_MAPS_API_KEY is not configured in .env. Please supply a valid Google Maps Platform API key.",
        }

    radius = max(1.0, min(50000.0, float(radius_meters)))
    count = max(1, min(20, int(max_results)))
    url = "https://places.googleapis.com/v1/places:searchNearby"

    payload = {
        "includedTypes": [place_type.strip().lower()],
        "maxResultCount": count,
        "locationRestriction": {
            "circle": {
                "center": {
                    "latitude": float(latitude),
                    "longitude": float(longitude),
                },
                "radius": radius,
            }
        },
    }

    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "X-Goog-Api-Key": api_key,
            "X-Goog-FieldMask": "places.displayName,places.formattedAddress,places.location,places.types,places.id",
            "User-Agent": "WanderWiseAgent/1.0",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        err_body = e.read().decode() if e.fp else ""
        return {"status": "error", "message": f"Places API HTTP error {e.code}: {err_body}"}
    except Exception as e:
        return {"status": "error", "message": f"Places API request failed: {str(e)}"}

    raw_places = data.get("places", [])
    places_out = []
    for p in raw_places:
        disp_name = p.get("displayName", {}).get("text", "")
        places_out.append({
            "name": disp_name,
            "formatted_address": p.get("formattedAddress"),
            "location": p.get("location"),
            "types": p.get("types", []),
            "place_id": p.get("id"),
        })

    return {
        "status": "success",
        "place_type": place_type,
        "count": len(places_out),
        "places": places_out,
    }


def suggest_roadtrip_detours(
    origin: str,
    destination: str,
    trip_theme: str = "spiritual",
    max_detour_minutes: int = 60,
) -> Dict[str, Any]:
    """Suggest 1-hour enjoyable detours along a driving road trip route between two locations.

    Uses free OpenStreetMap (Nominatim) and OSRM routing to calculate the driving corridor,
    total highway time, and identify towns and stops suitable for a quick 1-hour detour
    tailored to the traveler's theme (e.g. 'spiritual', 'fun', 'scenic', 'historic', 'food').

    Args:
        origin: Departure city or address (e.g. "Delhi", "San Francisco", "Rome").
        destination: Arrival city or address (e.g. "Agra", "Los Angeles", "Florence").
        trip_theme: The vibe of the detour ('spiritual', 'fun', 'scenic', 'historic', 'food'). Default: 'spiritual'.
        max_detour_minutes: Maximum detour driving time from the main corridor (default: 60 minutes).

    Returns:
        A dictionary with highway route summary, total distance, driving time, corridor waypoints/towns,
        and recommended 1-hour detour spots with visit recommendations.
    """
    headers = {"User-Agent": "WanderWiseTripAssistant/1.0"}

    # 1. Geocode origin and destination via OSM Nominatim
    def _osm_geocode(query: str):
        url = f"https://nominatim.openstreetmap.org/search?q={urllib.parse.quote(query.strip())}&format=json&limit=1"
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=10) as r:
            res = json.loads(r.read().decode())
        if not res:
            return None
        return {
            "name": query,
            "display_name": res[0].get("display_name"),
            "lat": float(res[0]["lat"]),
            "lon": float(res[0]["lon"]),
        }

    try:
        start_pt = _osm_geocode(origin)
        end_pt = _osm_geocode(destination)
    except Exception as e:
        return {"status": "error", "message": f"Geocoding error: {str(e)}"}

    if not start_pt or not end_pt:
        return {
            "status": "error",
            "message": f"Could not locate '{origin}' or '{destination}'. Please check city names.",
        }

    # 2. Compute driving route via free OSRM
    osrm_url = (
        f"https://router.project-osrm.org/route/v1/driving/"
        f"{start_pt['lon']},{start_pt['lat']};{end_pt['lon']},{end_pt['lat']}"
        f"?overview=full&geometries=geojson&steps=true"
    )
    req = urllib.request.Request(osrm_url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=12) as r:
            route_data = json.loads(r.read().decode())
    except Exception as e:
        return {"status": "error", "message": f"OSRM driving route request failed: {str(e)}"}

    if route_data.get("code") != "Ok" or not route_data.get("routes"):
        return {"status": "error", "message": "Failed to calculate road route between endpoints."}

    primary_route = route_data["routes"][0]
    total_km = round(primary_route["distance"] / 1000, 1)
    total_duration_hours = round(primary_route["duration"] / 3600, 1)

    # 3. Extract major corridor roads
    key_roads = []
    for step in primary_route.get("legs", [{}])[0].get("steps", []):
        rname = step.get("name")
        if rname and len(rname) > 3 and rname not in key_roads:
            key_roads.append(rname)

    # 4. Extract corridor waypoints at 33% and 66% of the trip
    coords = primary_route.get("geometry", {}).get("coordinates", [])
    corridor_stops = []
    if coords:
        for frac, label in [(0.33, "Early Stretch (~1/3 way)"), (0.66, "Mid-Late Stretch (~2/3 way)")]:
            idx = int(len(coords) * frac)
            lon_pt, lat_pt = coords[idx]
            rev_url = f"https://nominatim.openstreetmap.org/reverse?lat={lat_pt}&lon={lon_pt}&format=json"
            try:
                with urllib.request.urlopen(urllib.request.Request(rev_url, headers=headers), timeout=8) as rr:
                    r_data = json.loads(rr.read().decode())
                addr = r_data.get("address", {})
                town = addr.get("city") or addr.get("town") or addr.get("village") or addr.get("county") or "Route Corridor"
                state = addr.get("state") or addr.get("country") or ""
                corridor_stops.append({
                    "stretch": label,
                    "location_name": f"{town}, {state}".strip(", "),
                    "latitude": lat_pt,
                    "longitude": lon_pt,
                })
            except Exception:
                pass

    return {
        "status": "success",
        "route_summary": {
            "origin": start_pt["display_name"],
            "destination": end_pt["display_name"],
            "total_distance_km": total_km,
            "highway_drive_time_hours": total_duration_hours,
            "corridor_highways": key_roads[:6],
        },
        "trip_theme": trip_theme,
        "max_detour_minutes": max_detour_minutes,
        "corridor_waypoint_towns": corridor_stops,
        "map_source": "OpenStreetMap & OSRM (Free, No API Key Required)",
    }


async def generate_travel_image(
    item_name: str,
    visual_description: str,
    tool_context: ToolContext,
) -> Dict[str, Any]:
    """Generate a scenic travel photograph or postcard for a destination, attraction, or dish using Gemini image model.

    Args:
        item_name: Name of the travel spot, landmark, or dish (e.g. "Kyoto Bamboo Grove", "Eiffel Tower").
        visual_description: Brief description of the visual scene or style to generate.
        tool_context: The ADK tool execution context used to save the image as a session artifact.

    Returns:
        Status, public Cloud Storage image URL, and artifact filename.
    """
    clean_slug = "".join(c if c.isalnum() else "_" for c in item_name.lower()).strip("_")
    timestamp = datetime.datetime.now(ZoneInfo("UTC")).strftime("%Y%m%d_%H%M%S")
    filename = f"{clean_slug}_{timestamp}.jpg"

    prompt = (
        f"A beautiful, high quality travel photograph of {item_name}. "
        f"{visual_description}. Vibrant colors, realistic lighting, postcard quality."
    )

    try:
        response = genai_client.models.generate_content(
            model="gemini-3.1-flash-lite-image",
            contents=prompt,
        )

        image_bytes = None
        mime_type = "image/jpeg"
        if response.candidates and response.candidates[0].content and response.candidates[0].content.parts:
            for part in response.candidates[0].content.parts:
                if getattr(part, "inline_data", None) and getattr(part.inline_data, "data", None):
                    image_bytes = part.inline_data.data
                    mime_type = part.inline_data.mime_type or "image/jpeg"
                    break

        if not image_bytes:
            return {"status": "error", "message": f"No image bytes were returned by the model for '{item_name}'."}

        # 1. Save as ADK session artifact (shows up in Playground Artifacts panel)
        artifact_part = types.Part.from_bytes(data=image_bytes, mime_type=mime_type)
        try:
            await tool_context.save_artifact(filename=filename, artifact=artifact_part)
        except Exception as e:
            # Fallback if artifact service isn't active in current runner
            pass

        # 2. Upload to public Cloud Storage bucket
        bucket = storage_client.bucket(GCS_BUCKET_NAME)
        blob = bucket.blob(filename)
        blob.upload_from_string(image_bytes, content_type=mime_type)

        public_url = f"https://storage.googleapis.com/{GCS_BUCKET_NAME}/{filename}"

        return {
            "status": "success",
            "item_name": item_name,
            "artifact_filename": filename,
            "image_url": public_url,
            "message": f"Generated image for '{item_name}' and published to Cloud Storage.",
        }
    except Exception as e:
        return {"status": "error", "message": f"Failed to generate image: {str(e)}"}


SANDBOX_RESOURCE_NAME = (
    "projects/132885778126/locations/us-central1/reasoningEngines/1134563508713684992/sandboxEnvironments/30703312449830912"
)


# WRITE: after each turn, send the session to Memory Bank for extraction.
async def generate_memories_callback(callback_context: CallbackContext):
    try:
        await callback_context.add_session_to_memory()
    except Exception:
        # Non-blocking fallback if memory service is unreachable or in-memory
        pass
    return None


schema_manager = A2uiSchemaManager(
    version="0.8",
    catalogs=[BasicCatalog.get_config("0.8")],
)

a2ui_instruction = schema_manager.generate_system_prompt(
    role_description=(
        "You are Ghumo Duniya (घूमो दुनिया), a helpful travel concierge and trip planning AI agent. "
        "You remember the user's stated travel preferences, dietary restrictions, favorite destinations, "
        "all user places (cities, countries, landmarks, hotels, restaurants, and spots mentioned or visited), "
        "and facts from previous conversations and use them to personalize your recommendations and itineraries. "
        "You help travelers discover curated attractions, activities, and dining spots, assemble custom itineraries, "
        "check live destination weather conditions, geocode locations, find nearby places of interest, calculate trip budgets, "
        "plan road trips with 1-hour detour suggestions along highway corridors for spiritual, fun, scenic, or historic journeys, "
        "and generate scenic travel photos or postcards. "
        "IMPORTANT PRICING RULE: Always quote prices, costs, ticket fees, and travel budget estimates in Indian Rupees (INR - ₹) using the ₹ symbol. If working with overseas destinations, convert to and display in INR (₹). "
        "You also have a safe Python sandbox code executor to perform precise calculations, data analysis, conversions, "
        "or date math. When executing Python code in the sandbox, output Python code in a ```tool_code ... ``` block and always print the results. "
        "Always use your tools (`suggest_roadtrip_detours`, `search_travel_spots`, `save_travel_spot`, `save_user_itinerary`, `get_user_itinerary`, "
        "`estimate_trip_budget`, `get_destination_weather`, `geocode_address`, `find_nearby_places`, `generate_travel_image`, `LoadMemoryTool`) "
        "and your code execution sandbox to fetch real data, resolve addresses, compute route detours, create visual media, compute results, and persist user travel plans."
    ),
    workflow_description="Analyze the request, invoke necessary tools, and return structured UI when appropriate.",
    ui_description=(
        "Keep every surface tiny and flat: ONE Card > ONE Column > a few Text rows. "
        "Never nest a Card inside a Card. "
        "Use ONLY these components: Card, Column, Row, Text, and Image. Do not use "
        "Table or Heading (unsupported), or Buttons, actions, or forms (they do "
        "nothing in adk web). "
        "You may include one Image component, but only when you have a public https "
        "URL for the image (for example the URL an image tool returns after uploading "
        "to a public bucket). Set the Image url to that exact https link, for example "
        '{"Image": {"url": {"literalString": "https://..."}}}. Never point an '
        "Image at a bare filename, an artifact name, or a non-http(s) path. If you do "
        "not have a public URL, add a short Text line noting the image instead. "
        "No markdown in text; use the usageHint property ('h1', 'h2', 'body') for "
        "headings and emphasis. "
        "Output ONLY the raw A2UI JSON array — no prose, and never wrap it in "
        "<a2a_datapart_json> tags or 'kind'/'data'/'metadata' objects."
    ),
    include_schema=True,
    include_examples=True,
)

root_agent = Agent(
    name="root_agent",
    model=Gemini(
        model=MODEL,
        client_kwargs={
            "vertexai": True,
            "project": FIRESTORE_PROJECT_ID,
            "location": "global",
        },
        retry_options=types.HttpRetryOptions(attempts=3),
    ),
    instruction=a2ui_instruction,
    code_executor=AgentEngineSandboxCodeExecutor(
        sandbox_resource_name=SANDBOX_RESOURCE_NAME,
    ),
    tools=[
        LoadMemoryTool(),
        suggest_roadtrip_detours,
        search_travel_spots,
        save_travel_spot,
        save_user_itinerary,
        get_user_itinerary,
        estimate_trip_budget,
        get_destination_weather,
        geocode_address,
        find_nearby_places,
        generate_travel_image,
    ],
    after_model_callback=a2ui_callback,
    after_agent_callback=generate_memories_callback,
)

app = App(
    root_agent=root_agent,
    name="app",
)
