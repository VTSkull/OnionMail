import requests, gnupg, subprocess, re, os, json
from flask import Flask, request, jsonify, render_template, redirect
from markupsafe import Markup
from typing import Optional
# Ensure the Tor service is running
subprocess.run(["systemctl", "start", "tor"], check=True)

os.system("pwd")
session = requests.session()
session.proxies = {
    'http': 'socks5h://localhost:9050',
    'https': 'socks5h://localhost:9050'
}

gpg_home = os.path.expanduser("~/.gnupg")
gpg = gnupg.GPG(gnupghome=gpg_home)

def get_fingerprint_from_key(key: str) -> Optional[str]:
    # Import the key to get its fingerprint, but do not add to keyring
    import_result = gpg.import_keys(key)
    if import_result.fingerprints:
        return import_result.fingerprints[0]
    return None

app = Flask(__name__)

@app.route('/')
def home():
    added_messages = ""
    with open("messages.json", "r") as file:
        messages = json.load(file)
        file.close()
    for message in messages:
        #print(messages[message])
        messages[message]['message'] = messages[message]['message'].replace("\n", "<br>")
        messages[message]['returnAddress'] = messages[message]['returnAddress'].replace("\n", "<br>")
        added_messages += f"<div class='messageContainer'> <div class='sender'>{messages[message]['returnAddress']}</div> <div class='message'>{messages[message]['message']}</div> </div>"

    added_messages = Markup(added_messages)
    return render_template('index.html', added_messages=added_messages)


@app.route('/getPublicKey', methods=['GET'])
def get_public_key():
    with open("key_fingerprint", "r") as file:
        key_fingerprint = file.read().strip()
        file.close()
    return jsonify({"key": gpg.export_keys(key_fingerprint, armor=True)})


@app.route('/send', methods=['POST'])
def post():


    # Get and validate the request data
    data = request.form
    if "toAddress" not in data or "message" not in data:
        return jsonify({"error": "Invalid request"}), 400
    # if re.match("^[^.]+\\.[^.]+$", data["toAddress"]) is None:
    #     return jsonify({"error": "Invalid address format"}), 400


    # Fetch your public key
    return_key = requests.get("http://localhost:5000/getPublicKey").json().get("key")
    return_key = return_key.replace("\\n", "\n")
    if not return_key:
        return jsonify({"error": "Public key not found"}), 404


    # Fetch the public key from the target address
    try:
        response = session.get(f"http://{data['toAddress']}/getPublicKey")
        response.raise_for_status()  # Raise HTTPError for bad responses (4xx or 5xx)
    except requests.exceptions.RequestException as e:
        return jsonify({"error": f"Failed to fetch public key: {str(e)}"}), 500
    
    public_key = response.json().get("key")
    if not public_key:
        return jsonify({"error": "Public key not found"}), 404
    public_key = public_key.replace("\\n", "\n")

    fingerprint = get_fingerprint_from_key(public_key)
    if not fingerprint:
        return jsonify({"error": "Failed to get fingerprint from public key"}), 500


    # Check if the key is already in the keyring
    existing_keys = gpg.list_keys()
    fingerprints = [k['fingerprint'] for k in existing_keys]
    if fingerprint not in fingerprints:
        import_result = gpg.import_keys(public_key)
        if not import_result.results or not import_result.fingerprints:
            return jsonify({"error": "Failed to import public key"}), 500
        recipient_fingerprint = import_result.fingerprints[0]
    else:
        recipient_fingerprint = fingerprint


    # Encrypt the message using the fetched public key
    print("Public Key:", public_key)
    try:
        encrypted_data = gpg.encrypt(data["message"], recipient_fingerprint, always_trust=True)
        if not encrypted_data.ok:
            print(encrypted_data.status)
            print(encrypted_data.stderr)
            print(encrypted_data.ok)
            return jsonify({"error": "Encryption failed"}), 500
        encrypted_message = str(encrypted_data)
    except Exception as e:
        return jsonify({"error": f"Encryption error: {str(e)}"}), 500
    

    # Prepare the data to be sent
    payload = {
        "message": encrypted_message,
        "returnKey": return_key,
        "returnAddress": data["returnAddress"] if "returnAddress" in data else None
    }
    r = requests.post(f"http://{data['toAddress']}/recv", json=payload, proxies=session.proxies)
    if r.status_code != 200:
        return jsonify({"error": "Failed to send message"}), 500
    else:
        return redirect("/")
    return payload


@app.route('/recv', methods=['POST'])
def recv():
    # Get and validate the request data
    data = request.get_json()
    if not data or "message" not in data:
        return jsonify({"error": "Invalid request"}), 400

    if "returnKey" not in data:
        returnkey = None
    else:
        returnkey = data["returnKey"].replace("\\n", "\n")
        if not returnkey:
            return jsonify({"error": "Return key not found"}), 404
    
    if "returnAddress" not in data:
        return_address = None
    else:
        return_address = data["returnAddress"]

        # add address to addressbook
        if "returnAddress" in data and "returnKey" in data:
            with open("addressbook.json", "r") as file:
                addressbook = json.load(file)
                file.close()

            if data["returnAddress"] in addressbook:
                if addressbook[data["returnAddress"]] != data["returnKey"]:
                    addressbook[data["returnAddress"]+ " (old)"] = data["returnKey"]
                
            addressbook[data["returnAddress"]] = data["returnKey"]
            with open("addressbook.json", "w") as file:
                json.dump(addressbook, file, indent="\t")
                file.close()

    if not return_address:
        return_address = "Unknown Address"

    # Load existing messages or create a new dictionary
    with open("messages.json", "r") as file:
        messages = json.load(file)
        file.close()

    if not messages:
        messages = {}
        id = 1
    else:
        id = int(max(messages.keys())) + 1

    # Save the message with the incremented ID
    with open("messages.json", "w") as file:
        messages[str(id)] = {
            "returnKey": returnkey,
            "returnAddress": return_address,
            "message": data["message"]
        }
        json.dump(messages, file, indent="\t")
        file.close()

    return jsonify({"status": "Message received"}), 200


if __name__ == '__main__':
    app.run(debug=True)