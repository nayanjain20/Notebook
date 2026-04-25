import os
import json
from flask import Flask, request, jsonify
from flask_cors import CORS
from openai import AzureOpenAI
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
CORS(app)

client = AzureOpenAI(
    api_key=os.getenv("AZURE_OPENAI_API_KEY"),
    api_version=os.getenv("AZURE_OPENAI_CHAT_API_VERSION"),
    azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
)
DEPLOYMENT = os.getenv("AZURE_OPENAI_CHAT_DEPLOYMENT")

@app.route('/')
def index():
    return jsonify({"message": "Hello from GemBot Flask API!"})

@app.route('/api/get_response', methods=['POST'])
def get_response():
    data = request.get_json()
    if not data:
        return jsonify({"error": "Request body must be JSON"}), 400

    user_message = data.get('message', '').strip()
    if not user_message:
        return jsonify({"error": "message is required"}), 400

    # Frontend uses role "model"; OpenAI expects "assistant"
    history = data.get('history', [])
    openai_messages = [{"role": "system", "content": "You are a helpful assistant."}]
    for msg in history:
        role = "assistant" if msg.get("role") == "model" else "user"
        for part in msg.get("parts", []):
            if "text" in part and part["text"]:
                openai_messages.append({"role": role, "content": part["text"]})
                break

    openai_messages.append({"role": "user", "content": user_message})

    try:
        response = client.chat.completions.create(
            model=DEPLOYMENT,
            messages=openai_messages,
            max_completion_tokens=800,
            temperature=0.7,
        )
        bot_response = response.choices[0].message.content
        return jsonify({"response": bot_response})
    except Exception as e:
        app.logger.error(f"OpenAI API error: {e}")
        return jsonify({"error": "Failed to get a response from the AI service."}), 500

if __name__ == '__main__':
    app.run(debug=True, port=5000)