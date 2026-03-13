import yfinance as yf
import pandas as pd
import requests
import json
import os
import time
from datetime import datetime

# --- إعدادات تليجرام ---
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN', '')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID', '')

def send_telegram_message(message):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram tokens not set (Local run without TG notifications).")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        'chat_id': TELEGRAM_CHAT_ID,
        'text': message,
        'parse_mode': 'Markdown'
    }
    try:
        requests.post(url, json=payload)
    except Exception as e:
        print(f"Failed to send Telegram message: {e}")


STATE_FILE = "crypto_bot_state.json"

class CryptoLiquisHunterBot:
    def __init__(self, initial_balance=2500.0):
        # أهم 10 عملات رقمية
        self.symbols = [
            "BTC-USD",  # Bitcoin
            "ETH-USD",  # Ethereum
            "SOL-USD",  # Solana
            "BNB-USD",  # Binance Coin
            "XRP-USD",  # Ripple
            "ADA-USD",  # Cardano
            "DOGE-USD", # Dogecoin
            "AVAX-USD", # Avalanche
            "LINK-USD", # Chainlink
            "LTC-USD"   # Litecoin
        ]
        
        self.balance = initial_balance
        self.positions = {sym: None for sym in self.symbols}
        self.entry_prices = {sym: 0.0 for sym in self.symbols}
        self.history = {sym: [] for sym in self.symbols}
        
        self.load_state()

    def load_state(self):
        if os.path.exists(STATE_FILE):
            try:
                with open(STATE_FILE, "r") as f:
                    data = json.load(f)
                    self.balance = data.get("balance", self.balance)
                    self.positions = data.get("positions", self.positions)
                    self.entry_prices = data.get("entry_prices", self.entry_prices)
                    self.history = data.get("history", self.history)
                print(f"📂 State Loaded: Balance {self.balance:.2f}$")
            except Exception as e:
                print(f"Error loading state: {e}")

    def save_state(self):
        data = {
            "balance": self.balance,
            "positions": self.positions,
            "entry_prices": self.entry_prices,
            "history": self.history
        }
        try:
            with open(STATE_FILE, "w") as f:
                json.dump(data, f, indent=4)
        except Exception as e:
            print(f"Error saving state: {e}")

    def fetch_market_data(self, symbol):
        # 15m intervals are better for crypto liquidations than 5m
        data = yf.download(symbol, period="5d", interval="15m", progress=False)
        return data

    def check_scam_wicks(self, data, symbol):
        """
        استراتيجية Crypto: Scam Wicks & Liquidations Hunter (صائد التسييل والتلاعب)
        في الكريبتو، الحيتان بيعملوا شمعة بديل طويل جداً (Wick) يضرب وقوف الخسارة (Liquidations) بفوليوم عالي،
        وبعدين يعكسوا السعر فوراً. البوت ده بيدخل مع الانعكاس ده!
        """
        if len(data) < 21: return 0
        
        def get_val(row, col):
            if isinstance(data.columns, pd.MultiIndex): return row[(col, symbol)]
            else: return row[col]

        lookback_data = data.iloc[-21:-1]
        current_candle = data.iloc[-1]
        
        if isinstance(data.columns, pd.MultiIndex):
            previous_high = lookback_data[('High', symbol)].max()
            previous_low = lookback_data[('Low', symbol)].min()
            avg_volume = lookback_data[('Volume', symbol)].mean()
        else:
            previous_high = lookback_data['High'].max()
            previous_low = lookback_data['Low'].min()
            avg_volume = lookback_data['Volume'].mean()
            
        current_price = get_val(current_candle, 'Close')
        current_high = get_val(current_candle, 'High')
        current_low = get_val(current_candle, 'Low')
        current_vol = get_val(current_candle, 'Volume')
        
        # شرط الفوليوم الانفجاري (تأكيد حدوث تسييل أموال)
        is_liquidation_volume = current_vol > (1.5 * avg_volume)
        
        # 1. Fakeout Top (ضرب ستوبات البائعين) -> SELL SIGNAL
        if current_high > previous_high and current_price < previous_high and is_liquidation_volume:
            print(f"[{symbol}] 🚨 MASSIVE LONG LIQUIDATION WICK DETECTED! (Volume: {current_vol:.0f})")
            return -1
            
        # 2. Fakeout Bottom (ضرب ستوبات المشترين) -> BUY SIGNAL
        if current_low < previous_low and current_price > previous_low and is_liquidation_volume:
            print(f"[{symbol}] 🚨 MASSIVE SHORT LIQUIDATION WICK DETECTED! (Volume: {current_vol:.0f})")
            return 1
            
        return 0

    def execute_trade(self, symbol, signal, current_price):
        action_msg = None
        current_pos = self.positions[symbol]
        
        if current_pos is None:
            if signal == 1:
                self.positions[symbol] = "BUY"
                self.entry_prices[symbol] = float(current_price)
                action_msg = f"🦄 *SMART MONEY CRYPTO: BUY* 🟢\n🪙 *Coin:* `{symbol}`\n📍 *Entry:* `{current_price:.4f}`\n💰 *Total Balance:* `{self.balance:.2f}$`\n💥 _Reason: Short Liquidation Sweep_"
                print(action_msg)
            elif signal == -1:
                self.positions[symbol] = "SELL"
                self.entry_prices[symbol] = float(current_price)
                action_msg = f"🐻 *SMART MONEY CRYPTO: SELL* 🔴\n🪙 *Coin:* `{symbol}`\n📍 *Entry:* `{current_price:.4f}`\n💰 *Total Balance:* `{self.balance:.2f}$`\n💥 _Reason: Long Liquidation Sweep_"
                print(action_msg)
        else:
            # الخروج لو الإشارة اتعكست
            if (current_pos == "BUY" and signal == -1) or (current_pos == "SELL" and signal == 1):
                action_msg = self.close_position(symbol, float(current_price))
                
        self.save_state()
        return action_msg

    def close_position(self, symbol, current_price):
        current_pos = self.positions[symbol]
        entry = self.entry_prices[symbol]
        
        # ربح/خسارة بالنسبة المئوية. افترضنا رافعة مالية (Leverage) x10 كمتوسط للكريبتو.
        LEVERAGE = 10
        if current_pos == "BUY":
            profit_loss = ((current_price - entry) / entry) * self.balance * LEVERAGE
        elif current_pos == "SELL":
            profit_loss = ((entry - current_price) / entry) * self.balance * LEVERAGE
            
        self.balance += profit_loss
        status = "🟢 WIN" if profit_loss > 0 else "🔴 LOSS"
        
        msg = f"💸 *CRYPTO TRADE CLOSED* 💸\n🪙 *Coin:* `{symbol}`\n🔄 *Type:* `{current_pos}`\n💵 *P/L:* `{profit_loss:.2f}$` ({status})\n🏦 *New Balance:* `{self.balance:.2f}$`"
        print(msg)
        
        self.history[symbol].append({
            'Type': current_pos, 
            'Entry': entry, 
            'Exit': current_price, 
            'P/L': profit_loss, 
            'Status': status,
            'Time': datetime.now().strftime('%Y-%m-%d %H:%M')
        })
        
        self.positions[symbol] = None
        self.entry_prices[symbol] = 0.0
        
        self.send_symbol_summary(symbol)
        return msg

    def send_symbol_summary(self, symbol):
        hist = self.history[symbol]
        if not hist: return
            
        wins = sum(1 for t in hist if "WIN" in t.get('Status', ''))
        total = len(hist)
        win_rate = (wins / total) * 100 if total > 0 else 0
        total_profit = sum(t['P/L'] for t in hist)
        
        summary_msg = f"📊 *{symbol} Crypto Summary*\n"
        summary_msg += f"🏅 *Total P/L:* `{total_profit:.2f}$`\n"
        summary_msg += f"📈 *Win Rate:* `{win_rate:.1f}%` ({wins}/{total})\n\n"
        summary_msg += "📜 *Recent Trades:*\n"
        
        for t in hist[-3:]:
            emoji = "🟢" if "WIN" in t.get('Status', '') else "🔴"
            summary_msg += f"{emoji} {t['Type']} | P/L: `{t['P/L']:.2f}$`\n"
            
        print(summary_msg.replace('*', '').replace('`', ''))
        send_telegram_message(summary_msg)

    def run_all(self):
        print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Mapping Top 10 Crypto Markets for Liquidations...")
        for symbol in self.symbols:
            try:
                data = self.fetch_market_data(symbol)
                # تخطي العملة لو مفيش داتا 
                if data.empty or len(data) < 21:
                    continue
                    
                if isinstance(data.columns, pd.MultiIndex):
                    current_price = data.iloc[-1][('Close', symbol)]
                else:
                    current_price = data.iloc[-1]['Close']
                
                # الكريبتو معندوش Kill Zones شغال 24/7!
                signal = self.check_scam_wicks(data, symbol)
                
                msg = self.execute_trade(symbol, signal, current_price)
                if msg:
                    send_telegram_message(msg)
                
                if signal != 0:
                    print(f"[{symbol}] Scam Wick Confirmed! Price: {float(current_price):.4f} | Signal: {signal}")
                else:
                    print(f"[{symbol}] Price: {float(current_price):.4f} | Liquidations: Normal (0)")
                
            except Exception as e:
                print(f"[{symbol}] Error: {e}")
        print("-" * 60)

if __name__ == "__main__":
    bot = CryptoLiquisHunterBot()
    startup_msg = f"🚀 *Crypto Scam Wick Hunter Started!*\n🌐 *Monitoring:* {len(bot.symbols)} Coins\n💰 *Balance:* {bot.balance}$"
    print(startup_msg.replace('*', ''))
    
    # رسالة للتيست في تليجرام عشان نتأكد من الكريبتو بوت
    send_telegram_message(startup_msg)
    
    # لو شغالين على سيرفرات جيت هب (تتنفذ مرة واحدة وتقفل عشان توفر السيرفر)
    if os.getenv('GITHUB_ACTIONS'):
        bot.run_all()
    else:
        # لو شغالين لوكال (تفضل شغالة في لوب كل ربع ساعة)
        while True:
            bot.run_all()
            print("Waiting 15 minutes for the next crypto candle... ⏳")
            time.sleep(900)
