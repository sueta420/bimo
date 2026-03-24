import os
from dotenv import load_dotenv
from pybit.unified_trading import HTTP

load_dotenv('.env')

client = HTTP(api_key=os.getenv('BYBIT_API_KEY'), api_secret=os.getenv('BYBIT_API_SECRET'), testnet=False)
positions = client.get_positions(category='linear')['result']['list']
open_positions = [p for p in positions if float(p.get('size', 0)) > 0]
print(len(open_positions))
