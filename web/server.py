"""OSHMS 웹 서버.

Flask 기반 REST API + 반응형 웹 UI.
PC 브라우저와 모바일(Android) 브라우저 모두 지원.
홈 화면 추가(PWA)로 앱처럼 사용 가능.
"""

import json
import os
import threading
import time
from pathlib import Path

from flask import Flask, render_template, jsonify, request

from config.settings import Settings
from utils.logger import setup_logger

logger = setup_logger("oshms.web")

app = Flask(
    __name__,
    template_folder=str(Path(__file__).parent / "templates"),
    static_folder=str(Path(__file__).parent / "static"),
)
app.secret_key = os.urandom(24)

# 전역 상태
_state = {
    "trading": False,
    "trader": None,
    "thread": None,
    "logs": [],
    "settings": None,
    "api": None,  # KISApi 인스턴스 캐시 (토큰 재사용)
    "scheduler": None,  # v4.2: MarketScheduler 인스턴스
    "scheduler_enabled": False,
}


def _get_settings() -> Settings:
    if _state["settings"] is None:
        _state["settings"] = Settings.from_env()
    return _state["settings"]


def _get_api():
    """KISApi 인스턴스를 캐시하여 반환한다 (토큰 재사용)."""
    from api.kis_api import KISApi
    if _state["api"] is None:
        s = _get_settings()
        _state["api"] = KISApi(s)
    return _state["api"]


def _reset_api():
    """v4.7: 설정 변경 시 API만 재생성. Settings 객체는 유지하고 필드만 갱신.

    ``_state["settings"]``를 None으로 만들어 재생성하면 실행 중인 트레이더가
    예전 Settings 참조를 계속 물고 있게 되어 UI에서 바꾼 값이
    실제 매매에 반영되지 않는 버그의 원인이 된다.
    """
    s = _state.get("settings")
    if s is not None:
        s.reload_from_env()  # 제자리 갱신 — 모든 참조 홀더가 새 값을 봄
    _state["api"] = None

    # 실행 중인 트레이더의 API 참조도 교체 (자격증명 바뀌었을 수 있음)
    trader = _state.get("trader")
    if trader is not None:
        try:
            new_api = _get_api()
            trader.api = new_api
            if hasattr(trader, "order_manager"):
                trader.order_manager.api = new_api
        except Exception:
            pass


# ─────────────────── 페이지 ───────────────────

@app.route("/")
def index():
    return render_template("index.html")


# ─────────────────── REST API ───────────────────

@app.route("/api/debug/env")
def api_debug_env():
    """디버그: .env 파일 상태 확인 (키 값은 마스킹)."""
    import os
    env_path = Path(".env")
    result = {
        "env_exists": env_path.exists(),
        "env_path": str(env_path.resolve()),
        "working_dir": os.getcwd(),
    }

    if env_path.exists():
        lines = env_path.read_text(encoding="utf-8").strip().split("\n")
        env_parsed = {}
        for line in lines:
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip()
                # 비밀 값은 마스킹
                if "KEY" in k or "SECRET" in k:
                    if v:
                        env_parsed[k] = f"{v[:4]}***{v[-2:]} (길이={len(v)})"
                    else:
                        env_parsed[k] = "비어있음 ❌"
                else:
                    env_parsed[k] = v
        result["env_values"] = env_parsed
    else:
        result["env_values"] = {}
        result["error"] = ".env 파일이 없습니다"

    # 현재 메모리에 로드된 설정과 비교
    try:
        s = _get_settings()
        result["loaded_settings"] = {
            "app_key": f"{s.app_key[:4]}*** (길이={len(s.app_key)})" if s.app_key else "비어있음 ❌",
            "app_secret": f"{s.app_secret[:4]}*** (길이={len(s.app_secret)})" if s.app_secret else "비어있음 ❌",
            "account_no": s.account_no or "비어있음",
            "is_mock": s.is_mock,
            "base_url": s.base_url,
        }
    except Exception as e:
        result["loaded_settings_error"] = str(e)

    return jsonify(result)


@app.route("/api/status")
def api_status():
    try:
        s = _get_settings()
        trader = _state.get("trader")
        return jsonify({
            "trading": _state["trading"],
            "mock": s.is_mock,
            "account": f"{s.account_number}-{s.account_suffix}" if s.account_no else "",
            "api_configured": bool(s.app_key and s.app_secret),
            "active_market": trader.market if trader else None,
            "scheduler_enabled": _state.get("scheduler_enabled", False),
        })
    except Exception as e:
        return jsonify({
            "trading": False,
            "mock": True,
            "account": "",
            "api_configured": False,
            "error": str(e),
        })


@app.route("/api/balance")
def api_balance():
    try:
        api = _get_api()
        s = _get_settings()
        balance = api.get_balance()
        # 초기 자본금 정보 추가
        balance["initial_capital"] = s.initial_capital
        return jsonify(balance)
    except Exception as e:
        logger.warning("잔고 조회 실패: %s", e)
        # 에러 시에도 기본 구조는 반환 (UI가 최소한 동작하도록)
        s = _get_settings()
        return jsonify({
            "error": str(e),
            "holdings": [],
            "summary": {"available_cash": 0, "total_eval_amount": 0, "total_profit_loss": 0},
            "initial_capital": s.initial_capital,
        })


@app.route("/api/analyze", methods=["POST"])
def api_analyze():
    data = request.json
    code = data.get("code", "")
    name = data.get("name", code)

    try:
        from strategy.expert import ExpertStrategy
        from strategy.market_context import MarketContextAnalyzer

        s = _get_settings()
        api = _get_api()
        strategy = ExpertStrategy(api=api, settings=s)

        try:
            ctx = MarketContextAnalyzer(api).analyze()
            strategy.set_market_context(ctx)
        except Exception:
            pass

        current_price = api.get_current_price(code)
        if not current_price:
            return jsonify({"error": "시세 조회 실패"}), 400

        current_price["stock_name"] = name

        candles = api.get_minute_chart(code, period="3")
        if len(candles) < 20:
            candles = api.get_daily_chart(code, count=60)
        if not candles:
            return jsonify({"error": "차트 데이터 없음"}), 400

        analysis = strategy.full_analysis(code, name, candles, current_price)

        result = {
            "stock_code": code,
            "stock_name": name,
            "price": analysis.price,
            "decision": analysis.decision,
            "total_score": round(analysis.total_score, 3),
            "confidence": round(analysis.confidence, 2),
            "scores": {
                "technical": round(analysis.technical_score, 3),
                "pattern": round(analysis.pattern_score, 3),
                "sentiment": round(analysis.sentiment_score, 3),
                "market": round(analysis.market_score, 3),
                "price_level": round(analysis.price_level_score, 3),
            },
            "reasons": analysis.reasons,
            "news": analysis.sentiment.key_headlines if analysis.sentiment else [],
            "technical": {
                "rsi": round(analysis.technical.rsi, 1) if analysis.technical else 0,
                "macd": round(analysis.technical.macd_line, 0) if analysis.technical else 0,
                "bb_position": round(analysis.technical.bb_position, 2) if analysis.technical else 0,
                "volume_ratio": round(analysis.technical.volume_ratio, 1) if analysis.technical else 0,
                "ichimoku": analysis.technical.ichimoku_signal if analysis.technical else "",
            },
        }

        # AI 분석 리포트 추가
        try:
            from strategy.ai_analyst import AIAnalyst
            ai = AIAnalyst(api_key=s.openai_api_key)
            ai_data = AIAnalyst.extract_analysis_data(analysis)
            ai_result = ai.analyze(ai_data)
            result["ai_report"] = ai_result.format_report()
            result["ai_decision"] = ai_result.ai_decision
            result["ai_provider"] = ai_result.provider
        except Exception as e:
            logger.warning("AI 분석 실패: %s", e)

        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def _start_trading_direct(market: str, strategy_name: str = "expert",
                          interval: int = 10, stocks: str = ""):
    """매매 스레드를 직접 생성한다 (스케줄러/API 공용)."""
    import logging

    class WebLogHandler(logging.Handler):
        def emit(self, record):
            msg = self.format(record)
            # 멀티라인 메시지를 줄별로 분리하여 추가
            for line in msg.split('\n'):
                line = line.strip()
                if line:
                    _state["logs"].append(line)
            if len(_state["logs"]) > 2000:
                _state["logs"] = _state["logs"][-1500:]

    handler = WebLogHandler()
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(logging.Formatter("[%(asctime)s] %(message)s", datefmt="%H:%M:%S"))

    def _run():
        logging.getLogger("oshms").addHandler(handler)
        try:
            from strategy import ExpertStrategy, ScalpingStrategy, MomentumStrategy, CombinedStrategy
            from trading.trader import AutoTrader

            s = _get_settings()
            api = _get_api()

            strategy_map = {
                "expert": lambda: ExpertStrategy(api=api, settings=s),
                "scalping": ScalpingStrategy,
                "momentum": MomentumStrategy,
                "combined": CombinedStrategy,
            }

            factory = strategy_map.get(strategy_name, strategy_map["expert"])
            strat = factory()

            trader = AutoTrader(api, s, strat, market=market)
            _state["trader"] = trader
            _state["trading"] = True

            target = [t.strip() for t in stocks.split(",") if t.strip()] or None
            trader.start(target_stocks=target, interval=interval)
        except Exception as e:
            _state["logs"].append(f"오류: {e}")
        finally:
            _state["trading"] = False
            _state["trader"] = None
            logging.getLogger("oshms").removeHandler(handler)

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    _state["thread"] = thread


@app.route("/api/trade/start", methods=["POST"])
def api_trade_start():
    if _state["trading"]:
        return jsonify({"error": "이미 실행 중"}), 400

    data = request.json or {}
    stocks = data.get("stocks", "")
    strategy_name = data.get("strategy", "expert")
    interval = int(data.get("interval", 10))
    market = data.get("market", "KR")

    _start_trading_direct(market, strategy_name, interval, stocks)
    return jsonify({"message": f"자동매매 시작 (시장: {market})"})


@app.route("/api/trade/stop", methods=["POST"])
def api_trade_stop():
    if _state["trader"]:
        _state["trader"].stop()
    _state["trading"] = False
    return jsonify({"message": "중지됨"})


@app.route("/api/scheduler/start", methods=["POST"])
def api_scheduler_start():
    """24시간 멀티마켓 자동 스케줄러를 시작한다."""
    data = request.json or {}
    markets = data.get("markets", ["KR", "NASD"])
    strategy_name = data.get("strategy", "expert")
    interval = int(data.get("interval", 10))

    from trading.market_scheduler import MarketScheduler

    if _state.get("scheduler") and _state["scheduler_enabled"]:
        return jsonify({"error": "스케줄러 이미 실행 중"}), 400

    scheduler = MarketScheduler(enabled_markets=markets)

    # ── 콜백: 시장 개장 시 자동 매매 시작 ──
    def on_start_trading(market_code):
        if _state["trading"]:
            if _state["trader"]:
                _state["trader"].set_market(market_code)
                _state["logs"].append(f"[스케줄러] 시장 전환: {market_code}")
            return
        _state["logs"].append(f"[스케줄러] {market_code} 시장 개장 → 자동매매 시작")
        # 직접 매매 스레드 생성 (HTTP 우회)
        _start_trading_direct(market_code, strategy_name, interval)

    # ── 콜백: 시장 마감 시 자동 매매 종료 ──
    def on_stop_trading(market_code):
        _state["logs"].append(f"[스케줄러] {market_code} 시장 마감 → 매매 종료")
        if _state["trader"]:
            _state["trader"].stop()

    # ── 콜백: 청산 ──
    def on_liquidate(market_code):
        _state["logs"].append(f"[스케줄러] {market_code} 청산 시간 → 포지션 청산 시작")

    scheduler.set_callback("start_trading", on_start_trading)
    scheduler.set_callback("stop_trading", on_stop_trading)
    scheduler.set_callback("liquidate", on_liquidate)
    scheduler.start()

    _state["scheduler"] = scheduler
    _state["scheduler_enabled"] = True

    return jsonify({
        "message": "24시간 자동 스케줄러 시작",
        "markets": markets,
    })


@app.route("/api/scheduler/stop", methods=["POST"])
def api_scheduler_stop():
    """스케줄러를 종료한다."""
    if _state.get("scheduler"):
        _state["scheduler"].stop()
        _state["scheduler"] = None
    _state["scheduler_enabled"] = False

    # 매매도 함께 종료
    if _state["trader"]:
        _state["trader"].stop()

    return jsonify({"message": "스케줄러 종료"})


@app.route("/api/scheduler/status")
def api_scheduler_status():
    """스케줄러 현재 상태."""
    from trading.market_scheduler import MARKETS, is_market_open, is_trading_time

    scheduler = _state.get("scheduler")
    if scheduler and _state["scheduler_enabled"]:
        return jsonify(scheduler.get_status())

    # 스케줄러 미실행 시에도 시장 상태는 보여줌
    from datetime import datetime
    now_str = datetime.now().strftime("%H:%M")
    market_status = {}
    for code, m in MARKETS.items():
        market_status[code] = {
            "name": m.name,
            "open": is_market_open(m, now_str),
            "trading": is_trading_time(m, now_str),
            "open_time": m.open_time,
            "close_time": m.close_time,
            "active": False,
            "is_overseas": m.is_overseas,
        }

    return jsonify({
        "running": False,
        "active_market": None,
        "enabled_markets": [],
        "market_status": market_status,
    })


@app.route("/api/scheduler/markets", methods=["POST"])
def api_scheduler_update_markets():
    """활성 시장 목록을 업데이트한다."""
    data = request.json or {}
    markets = data.get("markets", [])
    if _state.get("scheduler"):
        _state["scheduler"].update_enabled_markets(markets)
    return jsonify({"markets": markets})


# 서버 포트 (스케줄러 콜백에서 사용)
_server_port = 5000


@app.route("/api/logs")
def api_logs():
    since = int(request.args.get("since", 0))
    logs = _state["logs"][since:]
    return jsonify({"logs": logs, "total": len(_state["logs"])})


@app.route("/api/settings", methods=["GET"])
def api_get_settings():
    s = _get_settings()
    # 마스킹: 앞 4자리만 보이고 나머지는 *** (설정 확인용)
    def _mask(val: str) -> str:
        if not val:
            return ""
        if len(val) <= 6:
            return "***설정됨***"
        return val[:4] + "***" + val[-2:]

    return jsonify({
        "app_key": _mask(s.app_key),
        "app_secret": _mask(s.app_secret),
        "app_key_set": bool(s.app_key),
        "app_secret_set": bool(s.app_secret),
        "account_no": s.account_no,
        "is_mock": s.is_mock,
        "initial_capital": s.initial_capital,
        "max_buy_amount": s.max_buy_amount,
        "max_hold_count": s.max_hold_count,
        "stop_loss_pct": s.stop_loss_pct,
        "take_profit_pct": s.take_profit_pct,
        "trading_start_time": s.trading_start_time,
        "trading_end_time": s.trading_end_time,
        "openai_api_key": _mask(s.openai_api_key),
        "openai_api_key_set": bool(s.openai_api_key),
    })


@app.route("/api/settings", methods=["POST"])
def api_save_settings():
    data = request.json
    # 기존 설정 로드 — 비밀 값이 빈 칸이면 기존 값 유지
    existing = _get_settings()

    env_lines = []

    # API 키: 빈 값이거나 마스킹 값(***포함)이면 기존 값 유지
    app_key = data.get("app_key", "")
    if not app_key or "***" in app_key:
        app_key = existing.app_key
    env_lines.append(f"KIS_APP_KEY={app_key}")

    app_secret = data.get("app_secret", "")
    if not app_secret or "***" in app_secret:
        app_secret = existing.app_secret
    env_lines.append(f"KIS_APP_SECRET={app_secret}")

    env_lines.append(f"KIS_ACCOUNT_NO={data.get('account_no', existing.account_no)}")
    env_lines.append(f"KIS_MOCK={'true' if data.get('is_mock', existing.is_mock) else 'false'}")
    env_lines.append(f"INITIAL_CAPITAL={data.get('initial_capital', existing.initial_capital)}")
    env_lines.append(f"MAX_BUY_AMOUNT={data.get('max_buy_amount', existing.max_buy_amount)}")
    env_lines.append(f"MAX_HOLD_COUNT={data.get('max_hold_count', existing.max_hold_count)}")
    env_lines.append(f"STOP_LOSS_PCT={data.get('stop_loss_pct', existing.stop_loss_pct)}")
    env_lines.append(f"TAKE_PROFIT_PCT={data.get('take_profit_pct', existing.take_profit_pct)}")
    env_lines.append(f"TRADING_START_TIME={data.get('trading_start_time', existing.trading_start_time)}")
    env_lines.append(f"TRADING_END_TIME={data.get('trading_end_time', existing.trading_end_time)}")

    # AI API 키: 빈 값이거나 마스킹 값이면 기존 값 유지
    openai_key = data.get("openai_api_key", "")
    if not openai_key or "***" in openai_key:
        openai_key = existing.openai_api_key
    if openai_key:
        env_lines.append(f"OPENAI_API_KEY={openai_key}")

    env_lines.append("LOG_LEVEL=INFO")

    Path(".env").write_text("\n".join(env_lines) + "\n", encoding="utf-8")
    _reset_api()  # 설정 변경 시 API 인스턴스 재생성 (토큰 재발급)
    return jsonify({"message": "저장 완료"})


@app.route("/api/trade/manual-sell", methods=["POST"])
def api_manual_sell():
    """보유 종목을 시장가로 강제 매도한다."""
    data = request.json or {}
    stock_code = data.get("stock_code", "").strip()
    if not stock_code:
        return jsonify({"error": "종목코드를 입력하세요"}), 400

    try:
        api = _get_api()
        balance = api.get_balance()

        # 보유 종목에서 해당 종목 찾기
        holding = None
        for h in balance.get("holdings", []):
            if h["stock_code"] == stock_code:
                holding = h
                break

        if not holding:
            return jsonify({"error": f"{stock_code} 종목을 보유하고 있지 않습니다"}), 400

        quantity = holding["quantity"]
        stock_name = holding.get("stock_name") or stock_code

        result = api.sell_market_order(stock_code, quantity)
        if not result["success"]:
            return jsonify({"error": f"매도 실패: {result.get('message', '알 수 없는 오류')}"}), 500

        # 거래 기록 저장
        _log_manual_trade(stock_code, stock_name, "SELL", quantity,
                          holding.get("current_price", 0), "사용자 강제 매도")

        logger.info("수동 매도 완료: %s(%s) %d주", stock_name, stock_code, quantity)
        return jsonify({
            "message": f"{stock_name} {quantity}주 매도 주문 완료",
            "stock_code": stock_code,
            "stock_name": stock_name,
            "quantity": quantity,
        })
    except Exception as e:
        logger.error("수동 매도 오류: %s", e)
        return jsonify({"error": str(e)}), 500


@app.route("/api/trade/manual-buy", methods=["POST"])
def api_manual_buy():
    """사용자가 지정한 종목을 시장가로 매수한다."""
    data = request.json or {}
    stock_code = data.get("stock_code", "").strip()
    amount = int(data.get("amount", 0))

    if not stock_code:
        return jsonify({"error": "종목코드를 입력하세요"}), 400

    try:
        api = _get_api()
        s = _get_settings()

        # 현재가 조회
        price_data = api.get_current_price(stock_code)
        if not price_data or not price_data.get("price"):
            return jsonify({"error": f"{stock_code} 시세 조회 실패 — 종목코드를 확인하세요"}), 400

        price = price_data["price"]
        stock_name = price_data.get("stock_name") or stock_code

        # 매수 금액 결정: 지정 금액 또는 설정의 최대 매수금액
        buy_amount = amount if amount > 0 else s.max_buy_amount
        quantity = buy_amount // price
        if quantity <= 0:
            return jsonify({"error": f"매수 수량 0: 가격 {price:,}원, 매수금액 {buy_amount:,}원"}), 400

        result = api.buy_market_order(stock_code, quantity)
        if not result["success"]:
            return jsonify({"error": f"매수 실패: {result.get('message', '알 수 없는 오류')}"}), 500

        # 거래 기록 저장
        _log_manual_trade(stock_code, stock_name, "BUY", quantity, price, "사용자 수동 매수")

        logger.info("수동 매수 완료: %s(%s) %d주 × %s원", stock_name, stock_code, quantity, f"{price:,}")
        return jsonify({
            "message": f"{stock_name} {quantity}주 매수 주문 완료 ({price:,}원 × {quantity}주 = {price * quantity:,}원)",
            "stock_code": stock_code,
            "stock_name": stock_name,
            "quantity": quantity,
            "price": price,
            "total_amount": price * quantity,
        })
    except Exception as e:
        logger.error("수동 매수 오류: %s", e)
        return jsonify({"error": str(e)}), 500


def _log_manual_trade(stock_code: str, stock_name: str, side: str,
                      quantity: int, price: int, reason: str) -> None:
    """수동 거래를 trades.json에 기록한다."""
    from datetime import datetime
    trades_file = Path("logs/trades.json")
    trades = []
    if trades_file.exists():
        try:
            trades = json.loads(trades_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, TypeError):
            pass

    trades.append({
        "stock_code": stock_code,
        "stock_name": stock_name,
        "side": side,
        "quantity": quantity,
        "price": price,
        "amount": price * quantity,
        "reason": reason,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "profit_loss": 0,
        "profit_rate": 0.0,
    })
    trades_file.parent.mkdir(parents=True, exist_ok=True)
    trades_file.write_text(json.dumps(trades, ensure_ascii=False, indent=2), encoding="utf-8")


@app.route("/api/report")
def api_report():
    from analysis.analyzer import ProfitAnalyzer
    analyzer = ProfitAnalyzer()
    summary = analyzer.get_summary()

    # 진화 세대 정보 추가
    try:
        from learning.evolution import EvolutionEngine
        evo = EvolutionEngine()
        summary["generation"] = evo.state.generation
        summary["best_fitness"] = evo.state.best_fitness
        summary["active_rules"] = len(evo.state.active_rules)
    except Exception:
        summary["generation"] = 0

    # 코드 진화 엔진 정보 추가
    try:
        from learning.code_evolution import CodeEvolutionEngine
        ce = CodeEvolutionEngine()
        summary["code_evolution_cycle"] = ce.state.cycle
        summary["code_evolution_improvements"] = ce.state.total_improvements
    except Exception:
        summary["code_evolution_cycle"] = 0

    # 누적통계(StateManager) 병합
    try:
        from trading.state_manager import StateManager
        sm = StateManager()
        stats = sm.get_stats_summary()
        # StateManager의 누적값이 더 정확할 수 있음 — trades.json 기반 값과 비교
        if stats.get("total_trades", 0) > summary.get("total_trades", 0):
            summary["total_trades"] = stats["total_trades"]
            summary["win_rate"] = stats["win_rate"]
            summary["total_profit"] = stats["total_profit"]
    except Exception:
        pass

    return jsonify({
        "summary": summary,
        "daily": analyzer.get_daily_summary(),
        "by_stock": analyzer.get_stock_summary(),
    })


@app.route("/api/trades")
def api_trades():
    """개별 거래 내역을 반환한다 (최신순).

    v4.3: 종목명이 비어있으면 API 캐시에서 보충.
    """
    import json
    from pathlib import Path
    trades_file = Path("logs/trades.json")
    if not trades_file.exists():
        return jsonify({"trades": []})
    try:
        data = json.loads(trades_file.read_text(encoding="utf-8"))

        # 종목명 보충 — 비어있거나 코드와 동일하면 API에서 조회
        api = None
        for t in data:
            code = t.get("stock_code", "")
            name = t.get("stock_name", "")
            if code and (not name or name == code):
                try:
                    if api is None:
                        api = _get_api()
                    # v4.4: 캐시만이 아닌 get_stock_name() 호출 (API 조회 포함)
                    resolved = api.get_stock_name(code) if hasattr(api, 'get_stock_name') else api._stock_name_cache.get(code, "")
                    if resolved and resolved != code:
                        t["stock_name"] = resolved
                except Exception:
                    continue  # v4.4: break→continue (한 종목 실패해도 나머지 계속)

        # 최신순 정렬 + 페이징
        data.reverse()
        page = int(request.args.get("page", 1))
        per_page = int(request.args.get("per_page", 20))
        total = len(data)
        start = (page - 1) * per_page
        end = start + per_page
        return jsonify({
            "trades": data[start:end],
            "total": total,
            "page": page,
            "per_page": per_page,
            "total_pages": (total + per_page - 1) // per_page if per_page > 0 else 1,
        })
    except Exception as e:
        return jsonify({"trades": [], "error": str(e)})


def _live_or_fresh(trader_attr: str, fallback_cls):
    """v4.7: 실행 중인 트레이더가 들고 있는 인스턴스를 우선 반환.

    없으면 디스크 상태를 로드한 새 인스턴스 반환. 기존 구현은 항상 새
    인스턴스를 만들어 트레이더가 메모리에서 쌓은 상태(아직 파일에 저장되지
    않은 변경분)를 볼 수 없었음.
    """
    trader = _state.get("trader")
    if trader is not None:
        live = getattr(trader, trader_attr, None)
        if live is not None:
            return live
    return fallback_cls()


@app.route("/api/v32/ensemble")
def api_ensemble():
    """v3.2: 전략 앙상블 현황."""
    try:
        from strategy.combined import CombinedStrategy
        ensemble = _live_or_fresh("_ensemble", CombinedStrategy)
        return jsonify(ensemble.get_ensemble_summary())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/v32/qlearning")
def api_qlearning():
    """v3.2: Q-Learning 현황."""
    try:
        from learning.q_learning import QLearningAgent
        agent = _live_or_fresh("_q_agent", QLearningAgent)
        return jsonify(agent.get_summary())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/v32/portfolio")
def api_portfolio():
    """v3.2: 포트폴리오 최적화 현황."""
    try:
        from trading.portfolio_optimizer import PortfolioOptimizer
        optimizer = _live_or_fresh("_portfolio_optimizer", PortfolioOptimizer)
        return jsonify(optimizer.get_portfolio_report())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/v32/evolution")
def api_evolution():
    """v3.2: 코드 진화 + 매매 복기 현황."""
    try:
        from learning.code_evolution import CodeEvolutionEngine
        engine = _live_or_fresh("_code_evolution", CodeEvolutionEngine)
        summary = engine.get_evolution_summary()
        summary["review_report"] = engine.get_trade_review_report()
        return jsonify(summary)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/evolution/history")
def api_evolution_history():
    """진화 내역 상세 — 적합도 추이, 활성 규칙, 가중치 변경, 코드진화 로드맵."""
    result = {}

    # 파라미터 진화 내역
    try:
        from learning.evolution import EvolutionEngine
        evo = EvolutionEngine()
        s = evo.state
        result["param"] = {
            "generation": s.generation,
            "best_fitness": s.best_fitness,
            "best_generation": s.best_generation,
            "last_evolution": s.last_evolution,
            "fitness_history": s.fitness_history[-10:],   # 최근 10세대
            "active_rules": s.active_rules,
            "weight_history": s.weight_history[-5:],      # 최근 5건
            "risk_params": s.risk_params,
            "risk_evolution_history": s.risk_evolution_history[-5:],
        }
    except Exception as e:
        result["param"] = {"error": str(e)}

    # 코드 자체 진화 내역
    try:
        from learning.code_evolution import CodeEvolutionEngine
        ce = CodeEvolutionEngine()
        cs = ce.state
        result["code"] = {
            "cycle": cs.cycle,
            "total_improvements": cs.total_improvements,
            "total_rollbacks": cs.total_rollbacks,
            "last_cycle": cs.last_cycle,
            "last_diagnosis": cs.last_diagnosis,
            "roadmap": cs.roadmap,
            "applied_modules": cs.applied_modules[-5:],   # 최근 5건
            "performance_history": cs.performance_history[-10:],
        }
    except Exception as e:
        result["code"] = {"error": str(e)}

    return jsonify(result)


@app.route("/api/evolution/schedule")
def api_evolution_schedule():
    """다음 진화 예정 시간 — 실시간 타이머용."""
    import time as _time
    trader = _state.get("trader")
    if not trader:
        return jsonify({"running": False, "message": "자동매매 미실행"})

    now = _time.time()
    result = {"running": True}

    # 파라미터 진화 스케줄
    try:
        last_evo = getattr(trader, '_last_evolution_time', 0)
        fallback = getattr(trader, '_EVOLUTION_TIME_FALLBACK', 1800)
        trades_since = getattr(trader, '_trades_since_evolution', 0)
        evo_interval = 3  # EvolutionEngine.EVOLUTION_INTERVAL
        if hasattr(trader, '_evolution') and trader._evolution:
            evo_interval = trader._evolution.EVOLUTION_INTERVAL

        elapsed = now - last_evo if last_evo > 0 else 0
        remaining_sec = max(0, fallback - elapsed) if last_evo > 0 else fallback
        result["param_evolution"] = {
            "trades_since": trades_since,
            "trades_needed": evo_interval,
            "time_remaining_sec": int(remaining_sec),
            "time_fallback_sec": fallback,
            "trigger": "거래" if trades_since >= evo_interval else "시간",
        }
    except Exception as e:
        result["param_evolution"] = {"error": str(e)}

    # 코드 진화 스케줄
    try:
        last_code = getattr(trader, '_last_code_evolution_time', 0)
        code_fallback = getattr(trader, '_CODE_EVOLUTION_TIME_FALLBACK', 2400)
        trades_since_code = getattr(trader, '_trades_since_code_evolution', 0)
        code_interval = 3  # CodeEvolutionEngine.CYCLE_INTERVAL
        if hasattr(trader, '_code_evolution') and trader._code_evolution:
            code_interval = trader._code_evolution.CYCLE_INTERVAL

        elapsed_code = now - last_code if last_code > 0 else 0
        remaining_code = max(0, code_fallback - elapsed_code) if last_code > 0 else code_fallback
        result["code_evolution"] = {
            "trades_since": trades_since_code,
            "trades_needed": code_interval,
            "time_remaining_sec": int(remaining_code),
            "time_fallback_sec": code_fallback,
            "trigger": "거래" if trades_since_code >= code_interval else "시간",
        }
    except Exception as e:
        result["code_evolution"] = {"error": str(e)}

    return jsonify(result)


def _check_port_available(host: str, port: int) -> bool:
    """포트가 사용 가능한지 확인한다."""
    import socket
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(1)
            s.bind((host, port))
            return True
    except OSError:
        return False


def _find_available_port(start_port: int = 5000, max_tries: int = 10) -> int:
    """사용 가능한 포트를 찾는다."""
    for p in range(start_port, start_port + max_tries):
        if _check_port_available("127.0.0.1", p):
            return p
    return start_port


def run_server(host="0.0.0.0", port=5000, debug=False):
    """웹 서버를 실행한다."""
    global _server_port
    import socket
    import webbrowser

    # 포트 충돌 확인
    if not _check_port_available(host if host != "0.0.0.0" else "127.0.0.1", port):
        old_port = port
        port = _find_available_port(port + 1)
        logger.warning("포트 %d 사용 중 → %d로 변경", old_port, port)
        print(f"⚠ 포트 {old_port} 사용 중 → {port}으로 변경합니다.")

    # 로컬 IP 주소 확인
    local_ip = "127.0.0.1"
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
    except Exception:
        pass

    logger.info("OSHMS 웹 서버 시작: http://%s:%d", host, port)
    print()
    print("=" * 55)
    print("  OSHMS 웹 서버 실행 중")
    print("=" * 55)
    print(f"  PC 브라우저:  http://localhost:{port}")
    print(f"  PC 브라우저:  http://127.0.0.1:{port}")
    if local_ip != "127.0.0.1":
        print(f"  모바일/다른PC: http://{local_ip}:{port}")
    print()
    print("  접속이 안 되면:")
    print(f"    1. 브라우저에서 http://localhost:{port} 으로 접속")
    print(f"    2. Windows 방화벽에서 Python 허용 확인")
    print(f"    3. python main.py web --port 8080 으로 포트 변경")
    print("=" * 55)
    print()

    # 자동으로 브라우저 열기
    try:
        webbrowser.open(f"http://localhost:{port}")
    except Exception:
        pass

    _server_port = port
    app.run(host=host, port=port, debug=debug, threaded=True)


if __name__ == "__main__":
    run_server(debug=True)
