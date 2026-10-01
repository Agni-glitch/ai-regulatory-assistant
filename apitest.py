import os
from dotenv import load_dotenv
from openai import AzureOpenAI

# 1. Load configuration from .env file
load_dotenv()

endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
api_key = os.getenv("AZURE_OPENAI_API_KEY")
api_version = os.getenv("AZURE_OPENAI_API_VERSION")
chat_deployment = os.getenv("AZURE_OPENAI_CHAT_DEPLOYMENT")
embedding_deployment = os.getenv("AZURE_OPENAI_EMBEDDING_DEPLOYMENT")

print("🔍 Configuration Loaded:")
print(f" - Endpoint: {endpoint}")
print(f" - API Version: {api_version}")
print(f" - Chat Deployment: {chat_deployment}")
print(f" - Embedding Deployment: {embedding_deployment}\n")

# 2. Initialize the Azure OpenAI Client
client = AzureOpenAI(
    azure_endpoint=endpoint,
    api_key=api_key,
    api_version=api_version,
)

# -----------------------------------------------------------------------------
# Test 1: Chat Completion Deployment
# -----------------------------------------------------------------------------
print("--- Testing Chat Deployment ---")
try:
    chat_response = client.chat.completions.create(
        model=chat_deployment,
        messages=[
            {"role": "system", "content": "You are a helpful assistant for ComplyNexus."},
            {"role": "user", "content": "Connection test. Respond with a brief success confirmation."}
        ],
        max_tokens=50
    )
    print("✅ Chat Deployment Success!")
    print(f"Response: {chat_response.choices[0].message.content.strip()}\n")
except Exception as e:
    print("❌ Chat Deployment Failed:")
    print(f"Error: {e}\n")

# -----------------------------------------------------------------------------
# Test 2: Embedding Deployment
# -----------------------------------------------------------------------------
print("--- Testing Embedding Deployment ---")
try:
    embedding_response = client.embeddings.create(
        model=embedding_deployment,
        input=["Testing vector embedding for compliance regulation text."]
    )
    vector = embedding_response.data[0].embedding
    print("✅ Embedding Deployment Success!")
    print(f"Generated Vector Length: {len(vector)} dimensions\n")
except Exception as e:
    print("❌ Embedding Deployment Failed:")
    print(f"Error: {e}\n")