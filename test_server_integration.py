import requests
import time

URL = "http://127.0.0.1:8000/process"
VIDEO_URL = "https://www.youtube.com/watch?v=-HZKqPZylr0"

def test_process():
    print(f"Sending request to {URL} for {VIDEO_URL}...")
    try:
        response = requests.post(URL, json={"url": VIDEO_URL}, timeout=300) # 5 min timeout
        if response.status_code == 200:
            print("Success!")
            print(response.json())
        else:
            print(f"Failed with status {response.status_code}")
            print(response.text)
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    # Wait a bit for server to start if running immediately after
    time.sleep(5) 
    test_process()
