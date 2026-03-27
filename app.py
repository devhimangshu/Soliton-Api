from flask import Flask, request, jsonify
import asyncio
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad
import binascii
import aiohttp
import json
import like_pb2
import threading

# Config
TOKEN_BATCH_SIZE = 100

current_batch_indices = {}
batch_indices_lock = threading.Lock()

# ---------------- TOKEN SYSTEM ---------------- #

def get_next_batch_tokens(server_name, all_tokens):
    if not all_tokens:
        return []

    total = len(all_tokens)

    if total <= TOKEN_BATCH_SIZE:
        return all_tokens

    with batch_indices_lock:
        if server_name not in current_batch_indices:
            current_batch_indices[server_name] = 0

        start = current_batch_indices[server_name]
        end = start + TOKEN_BATCH_SIZE

        if end > total:
            batch = all_tokens[start:] + all_tokens[:end - total]
        else:
            batch = all_tokens[start:end]

        current_batch_indices[server_name] = (start + TOKEN_BATCH_SIZE) % total
        return batch


def load_tokens(server_name):
    if server_name == "IND":
        path = "token_ind.json"
    elif server_name in {"BR", "US", "SAC", "NA"}:
        path = "token_br.json"
    else:
        path = "token_bd.json"

    try:
        with open(path, "r") as f:
            tokens = json.load(f)

            # Filter bad tokens
            valid = [
                t for t in tokens
                if isinstance(t, dict)
                and "token" in t
                and t["token"] not in ["ERROR", "N/A", ""]
            ]
            return valid

    except Exception as e:
        print("Token load error:", e)
        return []


# ---------------- ENCRYPTION ---------------- #

def encrypt_message(data):
    key = b'Yg&tc%DEuh6%Zc^8'
    iv = b'6oyZDr22E3ychjM%'
    cipher = AES.new(key, AES.MODE_CBC, iv)
    padded = pad(data, AES.block_size)
    encrypted = cipher.encrypt(padded)
    return binascii.hexlify(encrypted).decode()


def create_payload(uid, region):
    msg = like_pb2.like()
    msg.uid = int(uid)
    msg.region = region
    return msg.SerializeToString()


# ---------------- LIKE REQUEST ---------------- #

async def send_single(sem, encrypted, token_dict, url):
    token = token_dict.get("token", "")
    if not token:
        return "Missing Token"

    # Exact headers from your original working code
    headers = {
        "Authorization": f"Bearer {token}",
        "User-Agent": "Dalvik/2.1.0",
        "Content-Type": "application/x-www-form-urlencoded"
    }

    async with sem:
        try:
            # We create a brand new ClientSession for every token.
            # This mimics your original code and prevents the 503 firewall block.
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url,
                    data=bytes.fromhex(encrypted),
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as res:
                    return res.status
        except asyncio.TimeoutError:
            return "Timeout"
        except Exception as e:
            return type(e).__name__


async def send_batch(uid, region, url, tokens):
    payload = create_payload(uid, region)
    encrypted = encrypt_message(payload)

    # Semaphore limits concurrency so Vercel does not crash from too many open sockets
    sem = asyncio.Semaphore(50)
    
    tasks = [send_single(sem, encrypted, t, url) for t in tokens]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    success = 0
    failed = 0
    debug_codes = {}

    for r in results:
        code_str = str(r)
        debug_codes[code_str] = debug_codes.get(code_str, 0) + 1
        
        if r == 200:
            success += 1
        else:
            failed += 1

    return success, failed, debug_codes


# ---------------- FLASK ---------------- #

app = Flask(__name__)

@app.route("/like", methods=["GET"])
def like():
    uid = request.args.get("uid")
    server = request.args.get("server_name", "").upper()

    if not uid or not server:
        return jsonify({"error": "uid and server_name required"}), 400

    tokens = load_tokens(server)

    if not tokens:
        return jsonify({"error": "No valid tokens"}), 500

    batch = get_next_batch_tokens(server, tokens)

    if server == "IND":
        url = "https://client.ind.freefiremobile.com/LikeProfile"
    elif server in {"BR", "US", "SAC", "NA"}:
        url = "https://client.us.freefiremobile.com/LikeProfile"
    else:
        url = "https://clientbp.ggblueshark.com/LikeProfile"

    try:
        success, failed, debug_codes = asyncio.run(send_batch(uid, server, url, batch))
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    return jsonify({
        "status": 1 if success > 0 else 0,
        "UID": int(uid),
        "LikesGivenByAPI": success,
        "FailedRequests": failed,
        "TotalTokensUsed": len(batch),
        "message": "Likes sent successfully" if success > 0 else "No likes sent",
        "debug_info": debug_codes
    })


@app.route("/")
def home():
    return jsonify({"status": "API running"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5080)
