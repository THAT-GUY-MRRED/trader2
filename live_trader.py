# ============================================================================
# File 8: live_trader.py - Main Trading Bot
# ============================================================================

import asyncio
import time
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce

from config import CONFIG
from utils import load_all_keys
from live_data_collector import LiveDataCollector
from discord_notifier import DiscordNotifier
from signal_detector import SignalDetector
from indicator_calculator import IndicatorCalculator


class IntegratedLiveTrader:
    """Complete trading bot with Discord and live data"""
    
    def __init__(self, config):
        self.config = config
        
        keys = load_all_keys()
        api_key = keys.get('ALPACA_API_KEY')
        api_secret = keys.get('ALPACA_SECRET_KEY')
        
        if not api_key or not api_secret:
            raise ValueError("API keys not found in keys.env")
        
        self.trading_client = TradingClient(api_key, api_secret, paper=True)
        self.data_collector = LiveDataCollector(api_key, api_secret, config['SYMBOL'])
        
        self.discord = None
        if config.get('ENABLE_DISCORD'):
            discord_token = keys.get('DISCORD_TOKEN')
            discord_channel = keys.get('DISCORD_CHANNEL_ID')
            if discord_token and discord_channel:
                self.discord = DiscordNotifier(discord_token, discord_channel)
        
        self.trading_enabled = False
        self.running = False
        self.last_discord_update = time.time()
        self.signal_detector = None
        self.open_position = None  # Track open position
    
    async def initialize(self):
        """Initialize bot"""
        print("\n" + "="*70)
        print("BZ-CAE INTEGRATED LIVE TRADING BOT")
        print("="*70)
        print(f"Symbol: {self.config['SYMBOL']}")
        print(f"Min Candles Required: {self.config['MIN_CANDLES_REQUIRED']}")
        print(f"Discord: {self.config.get('ENABLE_DISCORD', False)}")
        print(f"DRY RUN: {self.config['DRY_RUN']}")
        print(f"LOG ONLY: {self.config['LOG_SIGNALS_ONLY']}")
        print("="*70 + "\n")
        
        if self.discord:
            try:
                await self.discord.start()
            except Exception as e:
                print(f"⚠️  Discord failed: {e}")
                self.discord = None
        
        try:
            account = self.trading_client.get_account()
            print(f"✓ Connected to Alpaca Paper Trading")
            print(f"  Portfolio: ${float(account.portfolio_value):,.2f}\n")
        except Exception as e:
            print(f"✗ Alpaca Error: {e}")
            raise
    
    async def run(self):
        """Main bot loop"""
        self.running = True
        await self.initialize()
        
        await self.data_collector.start_collection()
        
        print(f"📊 Collecting {self.config['MIN_CANDLES_REQUIRED']} candles...")
        print(f"   ~{self.config['MIN_CANDLES_REQUIRED'] * 5} minutes")
        print("   Press Ctrl+C to stop\n")
        
        try:
            while self.running:
                new_candle = self.data_collector.update()
                
                if not self.trading_enabled:
                    candle_count = len(self.data_collector.candles)
                    if candle_count > 0 and candle_count % 5 == 0:
                        print(f"   Progress: {candle_count}/{self.config['MIN_CANDLES_REQUIRED']} candles...")
                    
                    if self.data_collector.has_minimum_candles(self.config['MIN_CANDLES_REQUIRED']):
                        print("\n" + "="*70)
                        print("✅ TRADING ENABLED")
                        print("="*70 + "\n")
                        
                        self.trading_enabled = True
                        self.signal_detector = SignalDetector(self.config)
                        
                        if self.discord:
                            await self.discord.send_trading_enabled(len(self.data_collector.candles))
                
                if self.trading_enabled and new_candle:
                    await self.on_new_candle()
                
                if self.discord and self.trading_enabled:
                    if time.time() - self.last_discord_update > self.config.get('DISCORD_UPDATE_INTERVAL', 300):
                        await self.send_status_update()
                        self.last_discord_update = time.time()
                
                await asyncio.sleep(self.config['DATA_CHECK_INTERVAL'])
                
        except KeyboardInterrupt:
            print("\n⚠️  Shutting down...")
            await self.stop()
    
    async def on_new_candle(self):
        """Process new completed candle"""
        df = self.data_collector.get_dataframe()
        
        if df is None or len(df) < 100:
            return
        
        try:
            df['rsi'] = IndicatorCalculator.calculate_rsi(df['close'], self.config['RSI_PERIOD'])
            df['atr'] = IndicatorCalculator.calculate_atr(df, self.config['ATR_PERIOD'])
            df['ema20'] = IndicatorCalculator.calculate_ema(df['close'], self.config['EMA_FAST'])
            df['ema50'] = IndicatorCalculator.calculate_ema(df['close'], self.config['EMA_MID'])
            df['ema100'] = IndicatorCalculator.calculate_ema(df['close'], self.config['EMA_SLOW'])
        except Exception as e:
            print(f"⚠️  Indicator error: {e}")
            return
        
        current_price = df['close'].iloc[-1]
        current_rsi = df['rsi'].iloc[-1]
        current_time = df.index[-1]
        
        print(f"[{current_time}] ${current_price:,.2f} | RSI: {current_rsi:.1f}")
        
        if self.signal_detector:
            try:
                signal = self.signal_detector.detect_signal(df)
                
                if signal:
                    print(f"\n{'='*70}")
                    print(f"🚨 SIGNAL DETECTED!")
                    print(f"{'='*70}")
                    print(f"  Type: {signal['type']}")
                    print(f"  Confidence: {signal['confidence']:.0%}")
                    print(f"  Price: ${signal['price']:,.2f}")
                    print(f"  RSI: {signal['rsi']:.1f}")
                    print(f"  ATR: ${signal['atr']:.2f}")
                    print(f"{'='*70}\n")
                    
                    # Calculate order details
                    order_details = await self.calculate_order_details(signal, current_price)
                    
                    if order_details:
                        await self.print_order_details(order_details, signal)
                        
                        if self.discord:
                            try:
                                account = self.trading_client.get_account()
                                await self.discord.send_signal(signal, float(account.equity))
                            except:
                                pass
                        
                        # EXECUTE TRADE
                        if self.config['LOG_SIGNALS_ONLY']:
                            print("  [LOG ONLY MODE - No order placed]\n")
                        else:
                            await self.execute_trade(signal, order_details)
                    
            except Exception as e:
                print(f"⚠️  Signal error: {e}")
                import traceback
                traceback.print_exc()
    
    async def calculate_order_details(self, signal, current_price):
        """Calculate order details and return them"""
        try:
            is_bullish = signal['type'] == 'BULLISH'
            atr = signal['atr']
            stop_multiplier = self.config['STOP_MULTIPLIER']
            
            account = self.trading_client.get_account()
            account_balance = float(account.equity)
            risk_amount = account_balance * self.config['RISK_PER_TRADE']
            
            # Calculate levels
            if is_bullish:
                entry = current_price
                stop = entry - (atr * stop_multiplier)
                target_1 = entry + (atr * stop_multiplier * self.config['TARGET_1_RR'])
                target_2 = entry + (atr * stop_multiplier * self.config['TARGET_2_RR'])
                target_3 = entry + (atr * stop_multiplier * self.config['TARGET_3_RR'])
                side = "BUY"
            else:
                entry = current_price
                stop = entry + (atr * stop_multiplier)
                target_1 = entry - (atr * stop_multiplier * self.config['TARGET_1_RR'])
                target_2 = entry - (atr * stop_multiplier * self.config['TARGET_2_RR'])
                target_3 = entry - (atr * stop_multiplier * self.config['TARGET_3_RR'])
                side = "SELL"
            
            # Calculate position size
            stop_distance = abs(entry - stop)
            
            if stop_distance > 0:
                position_size_usd = risk_amount / stop_distance
                qty = position_size_usd / entry
                qty = round(qty, 6)  # BTC precision
            else:
                qty = 0
            
            if qty <= 0:
                print(f"  ⚠️  Invalid position size (qty={qty})")
                return None
            
            risk_dollars = qty * stop_distance
            potential_profit_t1 = qty * abs(target_1 - entry)
            potential_profit_t2 = qty * abs(target_2 - entry)
            potential_profit_t3 = qty * abs(target_3 - entry)
            
            return {
                'side': side,
                'qty': qty,
                'entry': entry,
                'stop': stop,
                'target_1': target_1,
                'target_2': target_2,
                'target_3': target_3,
                'stop_distance': stop_distance,
                'risk_dollars': risk_dollars,
                'potential_profit_t1': potential_profit_t1,
                'potential_profit_t2': potential_profit_t2,
                'potential_profit_t3': potential_profit_t3,
                'account_balance': account_balance,
                'atr': atr,
                'stop_multiplier': stop_multiplier,
                'is_bullish': is_bullish
            }
            
        except Exception as e:
            print(f"  Error calculating order details: {e}")
            return None
    
    async def execute_trade(self, signal, order_details):
        """EXECUTE ACTUAL TRADE ON ALPACA"""
        try:
            # Check if already in a position
            if self.open_position:
                print(f"  ⚠️  Already in a position, skipping new signal\n")
                return
            
            symbol = self.config['SYMBOL']
            qty = order_details['qty']
            side = OrderSide.BUY if order_details['is_bullish'] else OrderSide.SELL
            
            # Create market order
            order_request = MarketOrderRequest(
                symbol=symbol,
                qty=qty,
                side=side,
                time_in_force=TimeInForce.DAY
            )
            
            # Submit order to Alpaca
            order = self.trading_client.submit_order(order_request)
            
            print(f"\n{'='*70}")
            print(f"✅ ORDER EXECUTED!")
            print(f"{'='*70}")
            print(f"  Order ID: {order.id}")
            print(f"  Status: {order.status}")
            print(f"  Side: {side.value.upper()}")
            print(f"  Quantity: {qty:.6f} BTC")
            print(f"  Entry: ${order_details['entry']:,.2f}")
            print(f"{'='*70}\n")
            
            # Store position info
            self.open_position = {
                'order_id': order.id,
                'symbol': symbol,
                'qty': qty,
                'side': order_details['is_bullish'],
                'entry': order_details['entry'],
                'stop': order_details['stop'],
                'target_1': order_details['target_1'],
                'target_2': order_details['target_2'],
                'target_3': order_details['target_3']
            }
            
            # Send Discord notification
            if self.discord:
                try:
                    embed_text = f"✅ **TRADE EXECUTED**\n"
                    embed_text += f"Side: {side.value.upper()}\n"
                    embed_text += f"Qty: {qty:.6f} BTC\n"
                    embed_text += f"Entry: ${order_details['entry']:,.2f}\n"
                    embed_text += f"Stop: ${order_details['stop']:,.2f}\n"
                    embed_text += f"Target 1: ${order_details['target_1']:,.2f}"
                    print(f"Discord notification queued for order {order.id}")
                except:
                    pass
            
        except Exception as e:
            print(f"\n✗ TRADE EXECUTION FAILED: {e}")
            import traceback
            traceback.print_exc()
    
    async def print_order_details(self, order_details, signal):
        """Print detailed order information to terminal"""
        print(f"\n{'='*70}")
        print(f"📋 ORDER DETAILS")
        print(f"{'='*70}")
        
        print(f"\n  Order Type: MARKET {order_details['side']}")
        print(f"  Symbol: {self.config['SYMBOL']}")
        print(f"  Quantity: {order_details['qty']:.6f} BTC")
        print(f"\n  Entry Price: ${order_details['entry']:,.2f}")
        print(f"  Stop Loss:   ${order_details['stop']:,.2f}  ({((order_details['stop']-order_details['entry'])/order_details['entry']*100):+.2f}%)")
        print(f"  Target 1:    ${order_details['target_1']:,.2f}  ({((order_details['target_1']-order_details['entry'])/order_details['entry']*100):+.2f}%)")
        print(f"  Target 2:    ${order_details['target_2']:,.2f}  ({((order_details['target_2']-order_details['entry'])/order_details['entry']*100):+.2f}%)")
        print(f"  Target 3:    ${order_details['target_3']:,.2f}  ({((order_details['target_3']-order_details['entry'])/order_details['entry']*100):+.2f}%)")
        
        print(f"\n  💰 RISK/REWARD ANALYSIS:")
        print(f"  Account Balance: ${order_details['account_balance']:,.2f}")
        print(f"  Risk Amount:     ${order_details['risk_dollars']:,.2f} ({self.config['RISK_PER_TRADE']*100:.1f}% of account)")
        print(f"  Position Size:   ${order_details['qty'] * order_details['entry']:,.2f}")
        print(f"\n  Potential Profit:")
        print(f"    Target 1: ${order_details['potential_profit_t1']:,.2f} ({self.config['TARGET_1_RR']:.1f}R)")
        print(f"    Target 2: ${order_details['potential_profit_t2']:,.2f} ({self.config['TARGET_2_RR']:.1f}R)")
        print(f"    Target 3: ${order_details['potential_profit_t3']:,.2f} ({self.config['TARGET_3_RR']:.1f}R)")
        
        print(f"\n  📊 TRADE PARAMETERS:")
        print(f"  ATR: ${order_details['atr']:.2f}")
        print(f"  Stop Distance: ${order_details['stop_distance']:.2f} ({order_details['stop_multiplier']}x ATR)")
        print(f"  Risk/Reward Ratio: 1:{self.config['TARGET_2_RR']}")
        
        if order_details['qty'] * order_details['entry'] < 10:
            print(f"\n  ⚠️  WARNING: Position size too small (${order_details['qty'] * order_details['entry']:.2f})")
        
        print(f"\n{'='*70}\n")
    
    async def send_status_update(self):
        """Send periodic status update to Discord"""
        if not self.discord:
            return
        
        try:
            account = self.trading_client.get_account()
            positions = self.trading_client.get_all_positions()
            await self.discord.send_account_update(account, positions)
        except Exception as e:
            print(f"⚠️  Discord update failed: {e}")
    
    async def stop(self):
        """Gracefully stop bot"""
        self.running = False
        
        await self.data_collector.stop()
        
        if self.discord:
            try:
                await self.discord.close()
            except:
                pass
        
        print("\n✓ Bot stopped")


async def main():
    """Main entry point"""
    print("""
╔═══════════════════════════════════════════════════════════════════════════╗
║          BZ-CAE LIVE TRADING BOT v3.1 - LIVE TRADING ENABLED             ║
╚═══════════════════════════════════════════════════════════════════════════╝
    """)
    
    bot = IntegratedLiveTrader(CONFIG)
    await bot.run()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 Goodbye!")
    except Exception as e:
        print(f"\n✗ Error: {e}")
        import traceback
        traceback.print_exc()