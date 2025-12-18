# ============================================================================
# File 6: live_data_collector.py - Real-Time Data Collection via WebSocket
# ============================================================================

import pandas as pd
import asyncio
from alpaca.data.live import CryptoDataStream
from datetime import datetime, timedelta, timezone


class LiveDataCollector:
    """Collect live 5-minute OHLCV candles via WebSocket"""
    
    def __init__(self, api_key, api_secret, symbol='BTC/USD'):
        self.api_key = api_key
        self.api_secret = api_secret
        self.symbol = symbol
        self.candles = []
        self.current_candle = None
        self.candle_start = None
        self.interval = timedelta(minutes=5)
        
        self.stream = None
        self.latest_price = None
        self.latest_bar_data = None
        
    async def start_collection(self):
        """Start collecting candles via WebSocket"""
        print(f"🚀 Starting LIVE WebSocket stream for {self.symbol}")
        
        self.stream = CryptoDataStream(self.api_key, self.api_secret)
        
        # Subscribe to minute bars
        self.stream.subscribe_bars(self.on_bar, self.symbol)
        
        # Start the stream
        asyncio.create_task(self.stream.run())
        
        # Wait for stream to connect
        await asyncio.sleep(2)
        
        self.candle_start = datetime.now(timezone.utc)
        print(f"✓ WebSocket stream connected for {self.symbol}\n")
    
    async def on_bar(self, bar):
        """Handle incoming bar data from WebSocket"""
        try:
            # Each minute bar from Alpaca
            timestamp = bar.timestamp
            open_price = bar.open
            high_price = bar.high
            low_price = bar.low
            close_price = bar.close
            volume = bar.volume
            
            self.latest_price = close_price
            self.latest_bar_data = {
                'timestamp': timestamp,
                'open': open_price,
                'high': high_price,
                'low': low_price,
                'close': close_price,
                'volume': volume
            }
            
            # Aggregate minute bars into 5-minute candles
            await self.process_minute_bar()
            
        except Exception as e:
            print(f"⚠️  Error processing bar: {e}")
    
    async def process_minute_bar(self):
        """Aggregate minute bars into 5-minute candles"""
        try:
            if self.current_candle is None:
                self.current_candle = {
                    'timestamp': self.latest_bar_data['timestamp'],
                    'open': self.latest_bar_data['open'],
                    'high': self.latest_bar_data['high'],
                    'low': self.latest_bar_data['low'],
                    'close': self.latest_bar_data['close'],
                    'volume': self.latest_bar_data['volume'],
                    'bars_in_candle': 1
                }
                self.candle_start = self.latest_bar_data['timestamp']
            else:
                # Update current candle
                self.current_candle['high'] = max(
                    self.current_candle['high'],
                    self.latest_bar_data['high']
                )
                self.current_candle['low'] = min(
                    self.current_candle['low'],
                    self.latest_bar_data['low']
                )
                self.current_candle['close'] = self.latest_bar_data['close']
                self.current_candle['volume'] += self.latest_bar_data['volume']
                self.current_candle['bars_in_candle'] += 1
                
                # Check if 5 minutes have passed (5 minute bars)
                time_diff = self.latest_bar_data['timestamp'] - self.candle_start
                
                if time_diff >= self.interval or self.current_candle['bars_in_candle'] >= 5:
                    # Complete the 5-minute candle
                    candle_to_save = {
                        'timestamp': self.candle_start,
                        'open': self.current_candle['open'],
                        'high': self.current_candle['high'],
                        'low': self.current_candle['low'],
                        'close': self.current_candle['close'],
                        'volume': round(self.current_candle['volume'], 6)
                    }
                    
                    self.candles.append(candle_to_save)
                    
                    print(f"✓ Candle #{len(self.candles)}: "
                          f"O:{candle_to_save['open']:.2f} H:{candle_to_save['high']:.2f} "
                          f"L:{candle_to_save['low']:.2f} C:{candle_to_save['close']:.2f}")
                    
                    # Start new candle
                    self.current_candle = {
                        'timestamp': self.latest_bar_data['timestamp'],
                        'open': self.latest_bar_data['open'],
                        'high': self.latest_bar_data['high'],
                        'low': self.latest_bar_data['low'],
                        'close': self.latest_bar_data['close'],
                        'volume': self.latest_bar_data['volume'],
                        'bars_in_candle': 1
                    }
                    self.candle_start = self.latest_bar_data['timestamp']
        
        except Exception as e:
            print(f"⚠️  Error processing minute bar: {e}")
    
    async def update(self):
        """Called periodically to check for new candles (non-blocking)"""
        # WebSocket handles data automatically, just return if new candle
        if len(self.candles) > 0:
            return True
        return False
    
    def get_dataframe(self):
        """Get candles as DataFrame"""
        if not self.candles:
            return None
        
        df = pd.DataFrame(self.candles)
        df.set_index('timestamp', inplace=True)
        return df
    
    def has_minimum_candles(self, min_count):
        """Check if we have enough candles"""
        return len(self.candles) >= min_count
    
    async def stop(self):
        """Stop the WebSocket stream"""
        if self.stream:
            try:
                await self.stream.close()
                print("✓ WebSocket stream closed")
            except Exception as e:
                print(f"⚠️  Error closing stream: {e}")