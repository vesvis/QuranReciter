import requests
import json

def test_api(surah_number):
    url = f"http://api.alquran.cloud/v1/surah/{surah_number}/quran-uthmani"
    print(f"Testing URL: {url}")
    try:
        response = requests.get(url, timeout=10)
        print(f"Status Code: {response.status_code}")
        if response.status_code == 200:
            data = response.json()
            print("Success! First Ayah:")
            print(data["data"]["ayahs"][0]["text"])
        else:
            print("Failed.")
            print(response.text[:200])
    except Exception as e:
        print(f"Exception: {e}")

if __name__ == "__main__":
    test_api(1)
    test_api(112)
