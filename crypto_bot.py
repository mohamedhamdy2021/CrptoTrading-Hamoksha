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

POSITION_SIZE = 100       # كل صفقة بتدخل بـ 100 دولار ثابتة
BINANCE_FEE = 0.001       # عمولة Binance = 0.1% لكل عملية (شراء أو بيع)

class CryptoLiquisHunterBot:
    def __init__(self, initial_balance=1000.0):
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
        # توصيات لم تتنفذ بسبب عدم كفاية الرصيد
        self.shadow_trades = {sym: None for sym in self.symbols}
        self.shadow_entry_prices = {sym: 0.0 for sym in self.symbols}
        self.shadow_history = {sym: [] for sym in self.symbols}
        
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
                    self.shadow_trades = data.get("shadow_trades", self.shadow_trades)
                    self.shadow_entry_prices = data.get("shadow_entry_prices", self.shadow_entry_prices)
                    self.shadow_history = data.get("shadow_history", self.shadow_history)
                print(f"📂 State Loaded: Balance {self.balance:.2f}$")
            except Exception as e:
                print(f"Error loading state: {e}")

    def save_state(self):
        data = {
            "balance": self.balance,
            "positions": self.positions,
            "entry_prices": self.entry_prices,
            "history": self.history,
            "shadow_trades": self.shadow_trades,
            "shadow_entry_prices": self.shadow_entry_prices,
            "shadow_history": self.shadow_history
        }
        try:
            with open(STATE_FILE, "w") as f:
                json.dump(data, f, indent=4)
        except Exception as e:
            print(f"Error saving state: {e}")

    def get_open_trade_count(self):
        return sum(1 for v in self.positions.values() if v is not None)

    def get_locked_balance(self):
        return self.get_open_trade_count() * POSITION_SIZE

    def get_available_balance(self):
        return self.balance - self.get_locked_balance()

    def fetch_market_data(self, symbol):
        data = yf.download(symbol, period="5d", interval="15m", progress=False)
        return data

    def check_scam_wicks(self, data, symbol):
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
        
        is_liquidation_volume = current_vol > (1.5 * avg_volume)
        
        if current_high > previous_high and current_price < previous_high and is_liquidation_volume:
            print(f"[{symbol}] 🚨 MASSIVE LONG LIQUIDATION WICK DETECTED! (Volume: {current_vol:.0f})")
            return -1
            
        if current_low < previous_low and current_price > previous_low and is_liquidation_volume:
            print(f"[{symbol}] 🚨 MASSIVE SHORT LIQUIDATION WICK DETECTED! (Volume: {current_vol:.0f})")
            return 1
            
        return 0

    def execute_trade(self, symbol, signal, current_price):
        action_msg = None
        current_pos = self.positions[symbol]
        shadow_pos = self.shadow_trades[symbol]
        
        # --- أولاً: معالجة الـ Shadow Trades (التوصيات اللي مدخلتش بسبب الرصيد) ---
        if shadow_pos is not None:
            if (shadow_pos == "BUY" and signal == -1) or (shadow_pos == "SELL" and signal == 1):
                self.close_shadow_trade(symbol, float(current_price))
        
        # --- ثانياً: الصفقات الحقيقية ---
        if current_pos is None:
            if signal != 0:
                available = self.get_available_balance()
                entry_fee = POSITION_SIZE * BINANCE_FEE
                
                if available >= POSITION_SIZE:
                    # فيه رصيد كافي → نفتح صفقة حقيقية
                    direction = "BUY" if signal == 1 else "SELL"
                    self.positions[symbol] = direction
                    self.entry_prices[symbol] = float(current_price)
                    # خصم عمولة الدخول من الرصيد
                    self.balance -= entry_fee
                    
                    reason = "Short Liquidation Sweep" if signal == 1 else "Long Liquidation Sweep"
                    emoji = "🦄" if signal == 1 else "🐻"
                    color = "🟢" if signal == 1 else "🔴"
                    open_count = self.get_open_trade_count()
                    
                    action_msg = (f"{emoji} *CRYPTO: {direction}* {color}\n"
                                  f"🪙 *Coin:* `{symbol}`\n"
                                  f"📍 *Entry:* `{current_price:.4f}`\n"
                                  f"💵 *Deal Size:* `{POSITION_SIZE}$`\n"
                                  f"💸 *Entry Fee:* `-{entry_fee:.2f}$`\n"
                                  f"📂 *Open Trades:* `{open_count}/{len(self.symbols)}`\n"
                                  f"💰 *Balance:* `{self.balance:.2f}$`\n"
                                  f"💥 _Reason: {reason}_")
                    print(action_msg)
                else:
                    # مفيش رصيد كافي → نبعت توصية بس من غير ما ندخل
                    direction = "BUY" if signal == 1 else "SELL"
                    reason = "Short Liquidation Sweep" if signal == 1 else "Long Liquidation Sweep"
                    
                    # نسجل الصفقة كـ Shadow Trade عشان نتابع نتيجتها
                    self.shadow_trades[symbol] = direction
                    self.shadow_entry_prices[symbol] = float(current_price)
                    
                    open_count = self.get_open_trade_count()
                    action_msg = (f"⚠️ *SIGNAL (Not Enough Balance)* ⚠️\n"
                                  f"🪙 *Coin:* `{symbol}`\n"
                                  f"📍 *Recommended:* `{direction}` at `{current_price:.4f}`\n"
                                  f"📂 *Open Trades:* `{open_count}/{len(self.symbols)}`\n"
                                  f"💰 *Available:* `{available:.2f}$` (need `{POSITION_SIZE}$`)\n"
                                  f"💥 _Reason: {reason}_\n"
                                  f"📌 _Will track result without affecting balance_")
                    print(action_msg)
        else:
            # إغلاق صفقة حقيقية لو الإشارة اتعكست
            if (current_pos == "BUY" and signal == -1) or (current_pos == "SELL" and signal == 1):
                action_msg = self.close_position(symbol, float(current_price))
                
        self.save_state()
        return action_msg

    def close_position(self, symbol, current_price):
        current_pos = self.positions[symbol]
        entry = self.entry_prices[symbol]
        
        # حساب الربح/الخسارة بناءً على حجم صفقة ثابت 100$
        if current_pos == "BUY":
            gross_pnl = POSITION_SIZE * ((current_price - entry) / entry)
        elif current_pos == "SELL":
            gross_pnl = POSITION_SIZE * ((entry - current_price) / entry)
            
        # خصم عمولة الخروج (0.1%)
        exit_fee = POSITION_SIZE * BINANCE_FEE
        net_pnl = gross_pnl - exit_fee
        total_fees = (POSITION_SIZE * BINANCE_FEE) * 2  # عمولة الدخول + الخروج
            
        pct_change = ((current_price - entry) / entry) * 100
        self.balance += gross_pnl - exit_fee  # نضيف الربح الصافي (بعد خصم عمولة الخروج فقط، الدخول اتخصمت وقت الفتح)
        status = "🟢 WIN" if net_pnl > 0 else "🔴 LOSS"
        
        msg = (f"💸 *CRYPTO TRADE CLOSED* 💸\n"
               f"🪙 *Coin:* `{symbol}`\n"
               f"🔄 *Type:* `{current_pos}`\n"
               f"📍 *Entry:* `{entry:.4f}`\n"
               f"🏁 *Exit:* `{current_price:.4f}`\n"
               f"📊 *Change:* `{pct_change:+.2f}%`\n"
               f"💰 *Gross P/L:* `{gross_pnl:+.2f}$`\n"
               f"💸 *Total Fees:* `-{total_fees:.2f}$` (0.1% x2)\n"
               f"💵 *Net P/L:* `{net_pnl:+.2f}$` ({status})\n"
               f"🏦 *New Balance:* `{self.balance:.2f}$`")
        print(msg)
        
        self.history[symbol].append({
            'Type': current_pos, 
            'Entry': entry, 
            'Exit': current_price, 
            'Gross_PnL': round(gross_pnl, 2),
            'Fees': round(total_fees, 2),
            'P/L': round(net_pnl, 2), 
            'Status': status,
            'Time': datetime.now().strftime('%Y-%m-%d %H:%M')
        })
        
        self.positions[symbol] = None
        self.entry_prices[symbol] = 0.0
        
        self.send_symbol_summary(symbol)
        return msg

    def close_shadow_trade(self, symbol, current_price):
        """إغلاق توصية (Shadow Trade) وعرض نتيجتها من غير ما تأثر على الرصيد"""
        shadow_pos = self.shadow_trades[symbol]
        entry = self.shadow_entry_prices[symbol]
        
        if shadow_pos == "BUY":
            gross_pnl = POSITION_SIZE * ((current_price - entry) / entry)
        elif shadow_pos == "SELL":
            gross_pnl = POSITION_SIZE * ((entry - current_price) / entry)
            
        total_fees = (POSITION_SIZE * BINANCE_FEE) * 2
        net_pnl = gross_pnl - total_fees
        pct_change = ((current_price - entry) / entry) * 100
        status = "🟢 WIN" if net_pnl > 0 else "🔴 LOSS"
        
        msg = (f"👻 *SHADOW TRADE RESULT (Not in Balance)* 👻\n"
               f"🪙 *Coin:* `{symbol}`\n"
               f"🔄 *Type:* `{shadow_pos}`\n"
               f"📍 *Entry:* `{entry:.4f}`\n"
               f"🏁 *Exit:* `{current_price:.4f}`\n"
               f"📊 *Change:* `{pct_change:+.2f}%`\n"
               f"💵 *Would-be Net P/L:* `{net_pnl:+.2f}$` ({status})\n"
               f"📌 _This trade was NOT executed (insufficient balance)_")
        print(msg)
        send_telegram_message(msg)
        
        self.shadow_history[symbol].append({
            'Type': shadow_pos,
            'Entry': entry,
            'Exit': current_price,
            'P/L': round(net_pnl, 2),
            'Status': status,
            'Time': datetime.now().strftime('%Y-%m-%d %H:%M')
        })
        
        self.shadow_trades[symbol] = None
        self.shadow_entry_prices[symbol] = 0.0
        self.save_state()

    def send_symbol_summary(self, symbol):
        hist = self.history[symbol]
        if not hist: return
            
        wins = sum(1 for t in hist if "WIN" in t.get('Status', ''))
        total = len(hist)
        win_rate = (wins / total) * 100 if total > 0 else 0
        total_profit = sum(t['P/L'] for t in hist)
        total_fees = sum(t.get('Fees', 0) for t in hist)
        
        summary_msg = f"📊 *{symbol} Crypto Summary*\n"
        summary_msg += f"🏅 *Net P/L:* `{total_profit:+.2f}$`\n"
        summary_msg += f"💸 *Total Fees Paid:* `{total_fees:.2f}$`\n"
        summary_msg += f"📈 *Win Rate:* `{win_rate:.1f}%` ({wins}/{total})\n\n"
        summary_msg += "📜 *Recent Trades:*\n"
        
        for t in hist[-3:]:
            emoji = "🟢" if "WIN" in t.get('Status', '') else "🔴"
            summary_msg += f"{emoji} {t['Type']} | Entry: `{t['Entry']:.4f}` → Exit: `{t['Exit']:.4f}` | Net: `{t['P/L']:+.2f}$`\n"
            
        print(summary_msg.replace('*', '').replace('`', ''))
        send_telegram_message(summary_msg)

    def run_all(self):
        print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Scanning Top 10 Crypto Markets...")
        print(f"💰 Balance: {self.balance:.2f}$ | 📂 Open: {self.get_open_trade_count()}/{len(self.symbols)} | 🔓 Available: {self.get_available_balance():.2f}$")
        for symbol in self.symbols:
            try:
                data = self.fetch_market_data(symbol)
                if data.empty or len(data) < 21:
                    continue
                    
                if isinstance(data.columns, pd.MultiIndex):
                    current_price = data.iloc[-1][('Close', symbol)]
                else:
                    current_price = data.iloc[-1]['Close']
                
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
    print(f"🤖 Crypto Bot Ready | Balance: {bot.balance:.2f}$ | Monitoring: {len(bot.symbols)} Coins")
    
    if os.getenv('GITHUB_ACTIONS'):
        bot.run_all()
    else:
        while True:
            bot.run_all()
            print("Waiting 15 minutes for the next crypto candle... ⏳")
            time.sleep(900)
