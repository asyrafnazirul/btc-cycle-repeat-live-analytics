import os
import sys
import time
import requests
import pandas as pd
import plotly.graph_objects as go
from datetime import datetime, timezone
from nicegui import ui, run

# --- Configuration ---
CACHE_FILE = 'btc_hist_2022_2025_1h.csv'
REFRESH_SECONDS = 60

HIST_START = datetime(2022, 1, 1, 0, 0, tzinfo=timezone.utc)
HIST_END   = datetime(2025, 12, 31, 23, 59, tzinfo=timezone.utc)
LIVE_START = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)

session = requests.Session()
session.headers.update({'User-Agent': 'Mozilla/5.0'})

def detect_best_provider():
    """Tests if Binance Vision CDN is reachable; falls back to Bitstamp if ISP-blocked."""
    try:
        r = session.get('https://data-api.binance.vision/api/v3/ping', timeout=4)
        if r.status_code == 200:
            print("Connected via Binance Vision Public Data CDN (BTCUSDT).")
            return 'binance_vision'
    except Exception:
        pass
    print("Binance domain blocked by ISP. Switched automatically to Bitstamp REST API (BTCUSD).")
    return 'bitstamp'

PROVIDER = detect_best_provider()

def fetch_ohlcv_range(start_ts_ms, end_ts_ms=None, show_progress=False):
    """Fetches 1H OHLCV candles (1,000 per batch) from the active unblocked provider."""
    since_ms = start_ts_ms
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    target_end_ms = min(end_ts_ms, now_ms) if end_ts_ms else now_ms
    all_ohlcv = []

    while since_ms < target_end_ms:
        try:
            if PROVIDER == 'binance_vision':
                params = {
                    'symbol': 'BTCUSDT',
                    'interval': '1h',
                    'startTime': since_ms,
                    'limit': 1000
                }
                if end_ts_ms:
                    params['endTime'] = target_end_ms
                r = session.get('https://data-api.binance.vision/api/v3/klines', params=params, timeout=10)
                r.raise_for_status()
                data = r.json()
                if not data:
                    break
                batch = [
                    [int(c[0]), float(c[1]), float(c[2]), float(c[3]), float(c[4]), float(c[5])]
                    for c in data
                ]
            else:
                # Bitstamp 1H OHLC (step=3600 seconds, limit=1000)
                start_sec = int(since_ms // 1000)
                params = {
                    'step': 3600,
                    'limit': 1000,
                    'start': start_sec
                }
                r = session.get('https://www.bitstamp.net/api/v2/ohlc/btcusd/', params=params, timeout=10)
                r.raise_for_status()
                ohlc_list = r.json().get('data', {}).get('ohlc', [])
                if not ohlc_list:
                    break
                batch = [
                    [int(c['timestamp']) * 1000, float(c['open']), float(c['high']),
                     float(c['low']), float(c['close']), float(c['volume'])]
                    for c in ohlc_list
                    if int(c['timestamp']) * 1000 <= target_end_ms
                ]

            if not batch:
                break

            if all_ohlcv and batch[0][0] <= all_ohlcv[-1][0]:
                batch = [c for c in batch if c[0] > all_ohlcv[-1][0]]
                if not batch:
                    break

            all_ohlcv.extend(batch)
            since_ms = batch[-1][0] + 3600_000

            if show_progress:
                print(f"  -> Downloaded {len(all_ohlcv):,} hourly candles...")

            if batch[-1][0] >= target_end_ms - 3600_000:
                break

            time.sleep(0.08)

        except Exception as e:
            print(f"Network error during batch fetch: {e}")
            break

    df = pd.DataFrame(all_ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    if not df.empty:
        df['datetime'] = pd.to_datetime(df['timestamp'], unit='ms', utc=True)
    return df

# --- State Manager ---
class CycleTracker:
    def __init__(self):
        self.df_hist = pd.DataFrame()
        self.df_live = pd.DataFrame()
        self.last_update_str = "Initializing..."
        self.matching_orig_date = ""
        self.matching_orig_price = 0.0

        # Offset in hours (-336 to +336)
        self.lead_lag_offset_hours = 0

        # JSON-safe string dates & float targets
        self.target_24h_date_str = ""
        self.target_24h_price = 0.0
        self.target_24h_pct = 0.0

        self.target_7d_date_str = ""
        self.target_7d_price = 0.0
        self.target_7d_pct = 0.0

        self.corr_30d = 0.0
        self.corr_ytd = 0.0

    def initialize_data(self):
        cache_valid = False
        if os.path.exists(CACHE_FILE) and os.path.getsize(CACHE_FILE) > 1024:
            try:
                temp_df = pd.read_csv(CACHE_FILE)
                if not temp_df.empty and 'datetime' in temp_df.columns:
                    self.df_hist = temp_df
                    self.df_hist['datetime'] = pd.to_datetime(self.df_hist['datetime'], utc=True)
                    cache_valid = True
                    print(f"Loaded {len(self.df_hist):,} cached 2022-2025 candles from '{CACHE_FILE}'.")
            except Exception:
                cache_valid = False

        if not cache_valid:
            print("Downloading Jan 2022 - Dec 2025 1H data (one-time setup, ~35,000 candles)...")
            self.df_hist = fetch_ohlcv_range(
                int(HIST_START.timestamp() * 1000),
                int(HIST_END.timestamp() * 1000),
                show_progress=True
            )
            if self.df_hist.empty:
                print("CRITICAL ERROR: Could not download historical data.")
                sys.exit(1)
            self.df_hist.to_csv(CACHE_FILE, index=False)
            print(f"Saved {len(self.df_hist):,} candles to '{CACHE_FILE}'.")

        self.df_hist['orig_date_str'] = self.df_hist['datetime'].dt.strftime('%Y-%m-%d %H:%M UTC')

        print("Fetching Jan 2026 - Present live 1H data...")
        self.df_live = fetch_ohlcv_range(int(LIVE_START.timestamp() * 1000), show_progress=True)
        if self.df_live.empty:
            print("CRITICAL ERROR: Could not download 2026 live data.")
            sys.exit(1)

        self.recalculate_projections()

    def update_live_tail(self):
        if self.df_live.empty:
            return
        last_ts = int(self.df_live['timestamp'].iloc[-1]) - (2 * 3600 * 1000)
        new_df = fetch_ohlcv_range(last_ts)
        if not new_df.empty:
            combined = pd.concat([self.df_live, new_df], ignore_index=True)
            self.df_live = (
                combined.drop_duplicates(subset=['timestamp'], keep='last')
                .sort_values('timestamp')
                .reset_index(drop=True)
            )
            self.recalculate_projections()

    def recalculate_projections(self):
        # 1. Base 4-year shift + Lead/Lag offset hours
        base_projected = self.df_hist['datetime'] + pd.DateOffset(years=4)
        self.df_hist['projected_date'] = base_projected + pd.Timedelta(hours=self.lead_lag_offset_hours)
        self.df_hist['projected_date_str'] = self.df_hist['projected_date'].dt.strftime('%Y-%m-%d %H:%M:%S')
        self.df_hist['hour_key'] = self.df_hist['projected_date'].dt.floor('h')

        self.df_live['datetime_str'] = self.df_live['datetime'].dt.strftime('%Y-%m-%d %H:%M:%S')
        self.df_live['hour_key'] = self.df_live['datetime'].dt.floor('h')

        # 2. Scaling calculations
        scale_start = float(self.df_live['close'].iloc[0]) / float(self.df_hist['close'].iloc[0])
        self.df_hist['scaled_to_start'] = self.df_hist['close'] * scale_start

        latest_time = self.df_live['datetime'].iloc[-1]
        latest_price = float(self.df_live['close'].iloc[-1])
        idx_now = (self.df_hist['projected_date'] - latest_time).abs().idxmin()
        hist_price_now = float(self.df_hist.loc[idx_now, 'close'])

        scale_now = latest_price / hist_price_now
        self.df_hist['scaled_to_now'] = self.df_hist['close'] * scale_now
        self.matching_orig_date = str(self.df_hist.loc[idx_now, 'orig_date_str'])
        self.matching_orig_price = hist_price_now

        # 3. Forward 24H & 7D Targets
        t_24h = latest_time + pd.Timedelta(hours=24)
        idx_24h = (self.df_hist['projected_date'] - t_24h).abs().idxmin()
        self.target_24h_date_str = str(self.df_hist.loc[idx_24h, 'projected_date_str'])
        self.target_24h_price = float(self.df_hist.loc[idx_24h, 'scaled_to_now'])
        self.target_24h_pct = ((self.target_24h_price - latest_price) / latest_price) * 100.0

        t_7d = latest_time + pd.Timedelta(days=7)
        idx_7d = (self.df_hist['projected_date'] - t_7d).abs().idxmin()
        self.target_7d_date_str = str(self.df_hist.loc[idx_7d, 'projected_date_str'])
        self.target_7d_price = float(self.df_hist.loc[idx_7d, 'scaled_to_now'])
        self.target_7d_pct = ((self.target_7d_price - latest_price) / latest_price) * 100.0

        # 4. Correlation Scores
        merged = pd.merge(
            self.df_live[['hour_key', 'close']],
            self.df_hist[['hour_key', 'close']],
            on='hour_key',
            suffixes=('_live', '_hist')
        )
        if len(merged) > 24:
            ytd_val = merged['close_live'].corr(merged['close_hist'])
            r30_val = merged.tail(720)['close_live'].corr(merged.tail(720)['close_hist'])
            self.corr_ytd = 0.0 if pd.isna(ytd_val) else float(ytd_val)
            self.corr_30d = 0.0 if pd.isna(r30_val) else float(r30_val)

        self.last_update_str = datetime.now().strftime('%H:%M:%S')

tracker = CycleTracker()
tracker.initialize_data()

# --- Build Plotly Figure ---
def create_figure():
    custom_hist = tracker.df_hist[['orig_date_str', 'close']].values.tolist()
    fig = go.Figure()

    # Trace 0: 2022-2025 Indexed to Jan 2026
    fig.add_trace(go.Scattergl(
        x=tracker.df_hist['projected_date_str'].tolist(),
        y=tracker.df_hist['scaled_to_start'].tolist(),
        mode='lines',
        name='2022–2025 Overlay (Indexed to Jan 2026)',
        customdata=custom_hist,
        hovertemplate="<b>2022–2025 (Indexed)</b><br>Orig Date: %{customdata[0]}<br>Orig Price: $%{customdata[1]:,.2f}<br>Scaled: $%{y:,.2f}<extra></extra>",
        line=dict(color='rgba(0, 212, 255, 0.45)', width=1.4)
    ))

    # Trace 1: 2022-2025 Fractal Anchored to Current Live Price
    fig.add_trace(go.Scattergl(
        x=tracker.df_hist['projected_date_str'].tolist(),
        y=tracker.df_hist['scaled_to_now'].tolist(),
        mode='lines',
        name='2022–2025 Fractal (Anchored to Live Price)',
        customdata=custom_hist,
        hovertemplate="<b>2022–2025 (Anchored Now)</b><br>Orig Date: %{customdata[0]}<br>Orig Price: $%{customdata[1]:,.2f}<br>Projected: $%{y:,.2f}<extra></extra>",
        line=dict(color='rgba(170, 102, 255, 0.75)', width=1.5, dash='dot')
    ))

    # Trace 2: Live BTC Price (2026-Present)
    fig.add_trace(go.Scattergl(
        x=tracker.df_live['datetime_str'].tolist(),
        y=tracker.df_live['close'].tolist(),
        mode='lines',
        name='Live BTC Price (2026–Present)',
        hovertemplate="<b>Live BTC</b>: $%{y:,.2f}<extra></extra>",
        line=dict(color='#F7931A', width=2.3)
    ))

    # Trace 3: Forward 24H & 7D Target Markers
    fig.add_trace(go.Scattergl(
        x=[tracker.target_24h_date_str, tracker.target_7d_date_str],
        y=[tracker.target_24h_price, tracker.target_7d_price],
        mode='markers+text',
        name='24H / 7D Fractal Targets',
        text=['24H Target', '7D Target'],
        textposition='top center',
        hovertemplate="<b>%{text}</b>: $%{y:,.2f}<extra></extra>",
        marker=dict(size=10, symbol='diamond', color=['#00E676', '#00B0FF'], line=dict(width=1, color='white'))
    ))

    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor="#121212",
        plot_bgcolor="#121212",
        hovermode="x unified",
        uirevision="keep_zoom_state",
        xaxis=dict(
            title="Timeline (Jan 2026 – Dec 2029)",
            range=['2026-01-01', '2029-12-31 23:59:59'],
            rangeselector=dict(
                bgcolor="#222", activecolor="#F7931A",
                buttons=[
                    dict(count=7, label="1W", step="day", stepmode="backward"),
                    dict(count=1, label="1M", step="month", stepmode="backward"),
                    dict(count=3, label="3M", step="month", stepmode="backward"),
                    dict(count=1, label="YTD", step="year", stepmode="todate"),
                    dict(step="all", label="Full 2026–2029")
                ]
            ),
            rangeslider=dict(visible=True, thickness=0.05)
        ),
        yaxis=dict(title="BTC Price (USD) - Log Scale", type="log"),
        legend=dict(yanchor="top", y=0.99, xanchor="left", x=0.01, bgcolor="rgba(0,0,0,0.6)"),
        margin=dict(l=50, r=30, t=50, b=40)
    )
    return fig

# --- NiceGUI Frontend Layout ---
@ui.page('/')
def main_page():
    ui.dark_mode().enable()

    with ui.header().classes('bg-neutral-900 flex-col gap-2 px-6 py-2 border-b border-neutral-800'):
        # Top Row: Title + Live Badge + Refresh Controls
        with ui.row().classes('w-full items-center justify-between'):
            with ui.row().classes('items-center gap-3'):
                ui.label('BTC 4-Year Cycle Live Overlay (1H)').classes('text-lg font-bold text-amber-500')
                price_badge = ui.badge('', color='amber').classes('text-sm font-mono px-3 py-1 text-black font-bold')
                hist_badge = ui.badge('', color='purple').classes('text-xs font-mono px-2 py-1')

            with ui.row().classes('items-center gap-4'):
                status_label = ui.label('').classes('text-xs text-neutral-400 font-mono')
                auto_switch = ui.switch('Auto-Refresh (60s)', value=True).classes('text-xs')
                ui.button('Refresh Now', on_click=lambda: trigger_refresh()).props('outline color=amber size=sm')

        # Middle Row: Lead / Lag Offset Slider & Reset Button
        with ui.row().classes('w-full items-center gap-4 py-1 border-t border-neutral-800/80'):
            ui.label('LEAD/LAG OFFSET:').classes('text-xs font-bold text-neutral-400 tracking-wider')
            
            offset_label = ui.label('0 hours (0.0 days)').classes('text-xs font-mono text-cyan-400 w-36')
            
            offset_slider = ui.slider(min=-336, max=336, step=1, value=0).classes('flex-grow max-w-md').props('label-always color=cyan')
            
            ui.button('Reset 0h', on_click=lambda: offset_slider.set_value(0)).props('flat dense size=sm color=grey').classes('text-xs')

        # Bottom Row: Forward 24H / 7D Fractal Targets & Correlation Scores
        with ui.row().classes('w-full items-center gap-3 pt-1 border-t border-neutral-800/80'):
            ui.label('FRACTAL PROJECTIONS & CORRELATION:').classes('text-xs font-bold text-neutral-400 tracking-wider')
            target_24h_badge = ui.badge('', color='green').classes('text-xs font-mono px-2.5 py-1 font-semibold')
            target_7d_badge = ui.badge('', color='green').classes('text-xs font-mono px-2.5 py-1 font-semibold')
            corr_30d_badge = ui.badge('', color='blue').classes('text-xs font-mono px-2.5 py-1')
            corr_ytd_badge = ui.badge('', color='cyan').classes('text-xs font-mono px-2.5 py-1')

    fig = create_figure()
    plot_widget = ui.plotly(fig).classes('w-full h-[calc(100vh-140px)]')

    def update_ui_elements():
        live_p = float(tracker.df_live['close'].iloc[-1])
        price_badge.set_text(f"Live: ${live_p:,.2f}")
        hist_badge.set_text(f"4Y Ago ({tracker.matching_orig_date}): ${tracker.matching_orig_price:,.2f}")

        # 24H Badge
        sign_24h = "+" if tracker.target_24h_pct >= 0 else ""
        color_24h = "positive" if tracker.target_24h_pct >= 0 else "negative"
        target_24h_badge.set_text(f"24H Target: ${tracker.target_24h_price:,.2f} ({sign_24h}{tracker.target_24h_pct:.2f}%)")
        target_24h_badge.props(f"color={color_24h}")

        # 7D Badge
        sign_7d = "+" if tracker.target_7d_pct >= 0 else ""
        color_7d = "positive" if tracker.target_7d_pct >= 0 else "negative"
        target_7d_badge.set_text(f"7D Target: ${tracker.target_7d_price:,.2f} ({sign_7d}{tracker.target_7d_pct:.2f}%)")
        target_7d_badge.props(f"color={color_7d}")

        # Correlation Badges
        corr_color = "positive" if tracker.corr_30d >= 0.5 else ("warning" if tracker.corr_30d >= 0.0 else "negative")
        corr_30d_badge.set_text(f"30D Rolling Corr: {tracker.corr_30d:+.2f}")
        corr_30d_badge.props(f"color={corr_color}")

        corr_ytd_badge.set_text(f"YTD Cycle Corr: {tracker.corr_ytd:+.2f}")
        status_label.set_text(f"Source: {PROVIDER} | Synced: {tracker.last_update_str}")

    def on_offset_change(e):
        hours = int(e.value)
        tracker.lead_lag_offset_hours = hours
        days = hours / 24.0
        offset_label.set_text(f"{hours:+d} hrs ({days:+.1f} days)")

        # Recalculate projections & correlation for new offset
        tracker.recalculate_projections()

        # Update Plotly traces in-place
        fig.data[0].x = tracker.df_hist['projected_date_str'].tolist()
        fig.data[0].y = tracker.df_hist['scaled_to_start'].tolist()
        fig.data[1].x = tracker.df_hist['projected_date_str'].tolist()
        fig.data[1].y = tracker.df_hist['scaled_to_now'].tolist()
        fig.data[3].x = [tracker.target_24h_date_str, tracker.target_7d_date_str]
        fig.data[3].y = [tracker.target_24h_price, tracker.target_7d_price]

        plot_widget.update()
        update_ui_elements()

    offset_slider.on('update:model-value', on_offset_change)

    async def trigger_refresh():
        status_label.set_text("Syncing live candle...")
        await run.io_bound(tracker.update_live_tail)

        fig.data[0].x = tracker.df_hist['projected_date_str'].tolist()
        fig.data[0].y = tracker.df_hist['scaled_to_start'].tolist()
        fig.data[1].x = tracker.df_hist['projected_date_str'].tolist()
        fig.data[1].y = tracker.df_hist['scaled_to_now'].tolist()
        fig.data[2].x = tracker.df_live['datetime_str'].tolist()
        fig.data[2].y = tracker.df_live['close'].tolist()
        fig.data[3].x = [tracker.target_24h_date_str, tracker.target_7d_date_str]
        fig.data[3].y = [tracker.target_24h_price, tracker.target_7d_price]

        plot_widget.update()
        update_ui_elements()

    update_ui_elements()
    ui.timer(REFRESH_SECONDS, lambda: trigger_refresh() if auto_switch.value else None)

ui.run(title='BTC 2026-2029 Cycle Predictor', port=8080, reload=False)