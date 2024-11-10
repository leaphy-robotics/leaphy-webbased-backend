# Leaphy webbased backend

## Running
Make a copy of `.env.example` named `.env`.
Fill in the path to `arduino-cli` and request an API key at https://console.groq.com/keys .

Install `requirements.txt` and run with:
```bash
python -m uvicorn --host 0.0.0.0 --port 5000 main:app
```
