"""프로그램 자체 진화 엔진 (메타 진화).

기존 EvolutionEngine이 트레이딩 전략 파라미터를 진화시키는 **하위 레벨** 엔진이라면,
CodeEvolutionEngine은 프로그램 자체의 로직과 기능을 자율적으로 개선하는 **상위 레벨** 엔진이다.

진화 계층:
  Level 0: 파라미터 튜닝 (기존 EvolutionEngine — 가중치, 임계값)
  Level 1: 전략 로직 진화 (매매 규칙 자동 생성/삭제/조합)
  Level 2: 기능 진화 (새 필터/모듈 활성화, 포지션 사이징 개선)
  Level 3: 아키텍처 진화 (전략 블렌딩, 앙상블 구성)

자율 진화 사이클 (사용자 개입 없이 자동 실행):
  1. 성능 진단 → 약점 식별 (어디서 돈을 잃는가?)
  2. 진화 모듈 우선순위 결정 (가장 임팩트 큰 개선은?)
  3. 변경 생성 → 백테스트 검증 → 적용
  4. 적용 후 모니터링 → 성과 비교 → 롤백/유지

v3.0 초기 버전
"""

import json
import math
import time
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from pathlib import Path

from utils.logger import setup_logger

logger = setup_logger("oshms.learning.code_evolution")

CODE_EVO_STATE_FILE = Path("data/code_evolution_state.json")
EVOLVED_CONFIG_FILE = Path("data/evolved_config.json")
TRADES_FILE = Path("logs/trades.json")


# ══════════════════════════════════════════════════
# 데이터 클래스
# ══════════════════════════════════════════════════

@dataclass
class PerformanceDiagnosis:
    """성능 진단 결과."""
    overall_score: float = 0           # 종합 점수 (0~100)
    win_rate: float = 0                # 승률 (%)
    profit_factor: float = 0           # 수익 팩터
    avg_profit: float = 0              # 평균 수익 (원)
    avg_loss: float = 0                # 평균 손실 (원)
    max_drawdown: float = 0            # 최대 연속 손실 (원)
    total_trades: int = 0              # 총 거래 수
    weaknesses: list[dict] = field(default_factory=list)  # 약점 목록
    strengths: list[dict] = field(default_factory=list)   # 강점 목록
    timestamp: str = ""


@dataclass
class ModuleResult:
    """진화 모듈 실행 결과."""
    module_id: str
    success: bool = False
    config_changes: dict = field(default_factory=dict)
    reason: str = ""
    backtest_score: float = 0
    estimated_impact: float = 0


@dataclass
class CodeEvolutionState:
    """코드 진화 엔진 상태."""
    cycle: int = 0
    # 개발 로드맵 (자동 생성/관리)
    roadmap: list[dict] = field(default_factory=list)
    # 적용된 진화 모듈 이력
    applied_modules: list[dict] = field(default_factory=list)
    # 성능 스냅샷 (진화 전후 비교용)
    performance_history: list[dict] = field(default_factory=list)
    # 현재 활성 진화 설정
    active_config: dict = field(default_factory=lambda: {
        "entry_signals": {},
        "exit_timing": {},
        "position_sizing": {},
        "time_filters": {"blocked_hours": [], "preferred_hours": []},
        "indicator_weights": {},
        "regime_strategies": {},
        "stock_filters": {},
        "risk_profile": {},
        "custom_rules": [],
    })
    # 롤백용 이전 설정
    previous_config: dict = field(default_factory=dict)
    # 메타데이터
    last_cycle: str = ""
    last_diagnosis: dict = field(default_factory=dict)
    total_improvements: int = 0
    total_rollbacks: int = 0
    # v3.2: 마지막 진화 시점 총 매도 수 (재시작 시 누적 카운팅용)
    last_evolve_sell_count: int = 0


# ══════════════════════════════════════════════════
# 메인 엔진
# ══════════════════════════════════════════════════

class CodeEvolutionEngine:
    """프로그램 자체 진화 엔진.

    프로그램이 스스로 자신의 약점을 파악하고, 개선 방안을 생성하고,
    검증 후 적용하는 완전 자율 시스템.

    사용법:
        engine = CodeEvolutionEngine()
        # 자동 매매 중 주기적 호출
        engine.run_evolution_cycle(trades, candles)
        # 현재 진화 설정 조회
        config = engine.get_active_config()
    """

    # 진화 사이클 간격 (거래 수 기준)
    CYCLE_INTERVAL = 20
    # 성능 악화 시 자동 롤백 임계값
    ROLLBACK_THRESHOLD = -15  # 적합도 15점 이상 하락 시
    # 최소 거래 수 (진화 시작 조건)
    MIN_TRADES_FOR_EVOLUTION = 15

    def __init__(self):
        self.state = self._load_state()
        self._init_roadmap()

    # ──────────────────────────────────────────────
    # 공개 API
    # ──────────────────────────────────────────────

    def run_evolution_cycle(self, trades: list[dict],
                            candles: list[dict] | None = None) -> dict:
        """자율 진화 사이클을 실행한다.

        Args:
            trades: 전체 거래 기록
            candles: 백테스트용 캔들 데이터 (선택)

        Returns:
            진화 결과 요약
        """
        sells = [t for t in trades if t.get("side") == "SELL"]
        if len(sells) < self.MIN_TRADES_FOR_EVOLUTION:
            return {"status": "skip", "reason": f"거래 부족 ({len(sells)}/{self.MIN_TRADES_FOR_EVOLUTION})"}

        self.state.cycle += 1
        cycle = self.state.cycle
        logger.info("═══ 코드 진화 사이클 #%d 시작 ═══", cycle)

        # 1단계: 성능 진단 + 심층 매매 복기
        diagnosis = self._diagnose_performance(sells)
        review = self._deep_review_trades(sells)
        diagnosis.weaknesses.extend(review.get("additional_weaknesses", []))
        self.state.last_diagnosis = asdict(diagnosis)
        self.state.last_diagnosis["trade_review"] = review

        # 성능 스냅샷 저장
        self.state.performance_history.append({
            "cycle": cycle,
            "score": diagnosis.overall_score,
            "win_rate": diagnosis.win_rate,
            "profit_factor": diagnosis.profit_factor,
            "total_trades": diagnosis.total_trades,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M"),
        })
        if len(self.state.performance_history) > 100:
            self.state.performance_history = self.state.performance_history[-100:]

        # 2단계: 성능 악화 체크 → 자동 롤백
        if self._should_rollback():
            self._rollback()
            self._save_state()
            return {"status": "rollback", "reason": "성능 악화로 이전 설정 복원",
                    "cycle": cycle}

        # 3단계: 로드맵 우선순위 갱신
        self._update_roadmap_priorities(diagnosis)

        # 4단계: 최우선 모듈 실행
        results = []
        executed = 0
        for module_def in sorted(self.state.roadmap,
                                  key=lambda m: m.get("priority", 0),
                                  reverse=True):
            if executed >= 2:  # 사이클당 최대 2개 모듈 실행
                break
            if module_def.get("status") == "cooldown":
                continue

            result = self._execute_module(module_def, sells, candles)
            if result.success:
                self._apply_module_result(module_def, result)
                executed += 1
            results.append(asdict(result))

        # 5단계: 진화 설정 저장
        self._save_evolved_config()
        self.state.last_cycle = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._save_state()

        summary = {
            "status": "evolved" if executed > 0 else "analyzed",
            "cycle": cycle,
            "diagnosis_score": diagnosis.overall_score,
            "weaknesses": len(diagnosis.weaknesses),
            "modules_executed": executed,
            "total_improvements": self.state.total_improvements,
            "results": results,
        }

        logger.info(
            "═══ 코드 진화 사이클 #%d 완료: 점수=%.1f 개선=%d건 ═══",
            cycle, diagnosis.overall_score, executed,
        )
        return summary

    def should_evolve(self, trades_since_last: int) -> bool:
        """진화 사이클 실행 시점인지 확인한다."""
        return trades_since_last >= self.CYCLE_INTERVAL

    def get_active_config(self) -> dict:
        """현재 활성화된 진화 설정을 반환한다."""
        return self.state.active_config

    def get_roadmap(self) -> list[dict]:
        """개발 로드맵을 반환한다 (우선순위 정렬)."""
        return sorted(self.state.roadmap,
                       key=lambda m: m.get("priority", 0),
                       reverse=True)

    def get_evolution_summary(self) -> dict:
        """진화 엔진 현황 요약."""
        summary = {
            "cycle": self.state.cycle,
            "total_improvements": self.state.total_improvements,
            "total_rollbacks": self.state.total_rollbacks,
            "last_cycle": self.state.last_cycle,
            "last_score": self.state.last_diagnosis.get("overall_score", 0),
            "roadmap_items": len(self.state.roadmap),
            "applied_modules": len(self.state.applied_modules),
            "active_config_keys": list(k for k, v in self.state.active_config.items() if v),
        }
        # 매매 복기 리포트 포함
        review = self.state.last_diagnosis.get("trade_review", {})
        if review:
            summary["trade_review"] = {
                "report": review.get("report", ""),
                "findings_count": len(review.get("findings", [])),
                "auto_applied": review.get("auto_applied", 0),
            }
        return summary

    def get_trade_review_report(self) -> str:
        """최근 매매 복기 리포트를 반환한다 (자연어)."""
        review = self.state.last_diagnosis.get("trade_review", {})
        return review.get("report", "아직 복기 데이터가 없습니다.")

    # ──────────────────────────────────────────────
    # 1단계: 성능 진단
    # ──────────────────────────────────────────────

    def _diagnose_performance(self, sells: list[dict]) -> PerformanceDiagnosis:
        """시스템 성능을 종합 진단한다."""
        d = PerformanceDiagnosis(
            timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            total_trades=len(sells),
        )

        if not sells:
            return d

        # 기본 지표
        wins = [t for t in sells if t.get("profit_loss", 0) > 0]
        losses = [t for t in sells if t.get("profit_loss", 0) < 0]

        d.win_rate = len(wins) / len(sells) * 100 if sells else 0
        d.avg_profit = sum(t["profit_loss"] for t in wins) / len(wins) if wins else 0
        d.avg_loss = sum(t["profit_loss"] for t in losses) / len(losses) if losses else 0
        total_win = sum(t["profit_loss"] for t in wins)
        total_loss = abs(sum(t["profit_loss"] for t in losses))
        d.profit_factor = total_win / total_loss if total_loss > 0 else 5.0

        # 최대 연속 손실
        max_dd = 0
        dd = 0
        for t in sells:
            if t.get("profit_loss", 0) < 0:
                dd += abs(t["profit_loss"])
                max_dd = max(max_dd, dd)
            else:
                dd = 0
        d.max_drawdown = max_dd

        # 종합 점수
        wr_score = min(d.win_rate, 80) / 80 * 30        # 승률 (30점)
        pf_score = min(d.profit_factor, 3) / 3 * 25     # 수익팩터 (25점)
        ratio = (d.avg_profit / abs(d.avg_loss)) if d.avg_loss != 0 else 2
        rr_score = min(ratio, 3) / 3 * 20               # 손익비 (20점)
        freq_score = min(len(sells) / 30, 1) * 15       # 거래빈도 (15점)
        dd_score = max(0, 10 - max_dd / 50000)          # 드로우다운 (10점)
        d.overall_score = round(wr_score + pf_score + rr_score + freq_score + dd_score, 1)

        # 약점 분석
        d.weaknesses = self._identify_weaknesses(sells, d)
        d.strengths = self._identify_strengths(sells, d)

        logger.info(
            "[진단] 점수=%.1f | 승률=%.1f%% | PF=%.2f | 약점=%d개",
            d.overall_score, d.win_rate, d.profit_factor, len(d.weaknesses),
        )
        return d

    def _identify_weaknesses(self, sells: list[dict],
                              d: PerformanceDiagnosis) -> list[dict]:
        """구체적인 약점을 식별한다."""
        weaknesses = []

        # 1. 낮은 승률
        if d.win_rate < 45:
            weaknesses.append({
                "area": "entry_signals",
                "severity": "high" if d.win_rate < 35 else "medium",
                "detail": f"승률 {d.win_rate:.1f}% — 진입 신호 정확도 부족",
                "suggested_module": "optimize_entry_signals",
            })

        # 2. 나쁜 손익비
        if d.avg_loss != 0 and d.avg_profit < abs(d.avg_loss) * 0.8:
            weaknesses.append({
                "area": "risk_profile",
                "severity": "high",
                "detail": f"손익비 불균형: 평균 수익 {d.avg_profit:,.0f}원 vs 평균 손실 {d.avg_loss:,.0f}원",
                "suggested_module": "optimize_risk_profile",
            })

        # 3. 큰 연속 손실
        if d.max_drawdown > 100000:
            weaknesses.append({
                "area": "risk_profile",
                "severity": "critical",
                "detail": f"최대 연속 손실 {d.max_drawdown:,.0f}원 — 리스크 관리 강화 필요",
                "suggested_module": "optimize_risk_profile",
            })

        # 4. 시간대별 약점
        time_weakness = self._analyze_time_weakness(sells)
        if time_weakness:
            weaknesses.append(time_weakness)

        # 5. 손절 빈도 분석
        stop_losses = [t for t in sells if "손절" in t.get("reason", "")]
        if len(stop_losses) > len(sells) * 0.3:
            weaknesses.append({
                "area": "entry_signals",
                "severity": "high",
                "detail": f"손절 비율 {len(stop_losses)/len(sells)*100:.0f}% — 진입 타이밍 개선 필요",
                "suggested_module": "optimize_entry_signals",
            })

        # 6. 트레일링스탑 효율
        trailing = [t for t in sells if "트레일링" in t.get("reason", "")]
        if trailing:
            avg_trail_profit = sum(t.get("profit_rate", 0) for t in trailing) / len(trailing)
            if avg_trail_profit < 1.5:
                weaknesses.append({
                    "area": "exit_timing",
                    "severity": "medium",
                    "detail": f"트레일링 평균 수익률 {avg_trail_profit:.1f}% — 너무 일찍 매도",
                    "suggested_module": "optimize_exit_timing",
                })

        # 7. 낮은 수익 팩터
        if d.profit_factor < 1.2:
            weaknesses.append({
                "area": "strategy_blend",
                "severity": "high" if d.profit_factor < 1.0 else "medium",
                "detail": f"수익 팩터 {d.profit_factor:.2f} — 전략 개선 필요",
                "suggested_module": "optimize_strategy_blend",
            })

        # 8. 포지션 사이징
        amounts = [t.get("amount", 0) for t in sells if t.get("amount", 0) > 0]
        if amounts:
            max_amt = max(amounts)
            min_amt = min(amounts)
            if max_amt > min_amt * 3:
                weaknesses.append({
                    "area": "position_sizing",
                    "severity": "low",
                    "detail": "포지션 크기 편차 큼 — 사이징 최적화 필요",
                    "suggested_module": "optimize_position_sizing",
                })

        return weaknesses

    def _identify_strengths(self, sells: list[dict],
                             d: PerformanceDiagnosis) -> list[dict]:
        """강점을 식별한다."""
        strengths = []
        if d.win_rate >= 55:
            strengths.append({"area": "entry_signals", "detail": f"승률 {d.win_rate:.1f}% 양호"})
        if d.profit_factor >= 1.5:
            strengths.append({"area": "overall", "detail": f"수익팩터 {d.profit_factor:.2f} 양호"})
        if d.avg_loss != 0 and d.avg_profit > abs(d.avg_loss) * 1.5:
            strengths.append({"area": "risk_profile", "detail": "손익비 양호"})
        return strengths

    def _analyze_time_weakness(self, sells: list[dict]) -> dict | None:
        """시간대별 약점을 분석한다."""
        by_hour = defaultdict(list)
        for t in sells:
            ts = t.get("timestamp", "")
            if len(ts) >= 13:
                hour = ts[11:13]
                by_hour[hour].append(t.get("profit_loss", 0))

        worst_hour = None
        worst_rate = 100
        for hour, profits in by_hour.items():
            if len(profits) < 3:
                continue
            wr = sum(1 for p in profits if p > 0) / len(profits) * 100
            if wr < worst_rate:
                worst_rate = wr
                worst_hour = hour

        if worst_hour and worst_rate < 30:
            return {
                "area": "time_filters",
                "severity": "medium",
                "detail": f"{worst_hour}시 승률 {worst_rate:.0f}% — 해당 시간대 회피 권장",
                "suggested_module": "optimize_time_filters",
            }
        return None

    # ──────────────────────────────────────────────
    # 2단계: 로드맵 관리
    # ──────────────────────────────────────────────

    def _init_roadmap(self):
        """초기 개발 로드맵을 구성한다."""
        if self.state.roadmap:
            return  # 이미 있으면 스킵

        modules = [
            {
                "id": "optimize_entry_signals",
                "name": "매수 진입 신호 최적화",
                "description": "매수 임계값, 신호 가중치, 확인 조건을 성과 데이터 기반으로 최적화",
                "category": "strategy",
                "priority": 0,
                "status": "pending",
                "cooldown_until": "",
            },
            {
                "id": "optimize_exit_timing",
                "name": "매도 타이밍 최적화",
                "description": "트레일링스탑, 익절, 목표가 도달 조건을 최적화하여 수익 극대화",
                "category": "strategy",
                "priority": 0,
                "status": "pending",
                "cooldown_until": "",
            },
            {
                "id": "optimize_risk_profile",
                "name": "리스크 프로필 진화",
                "description": "손절/익절/포지션 크기를 성과와 시장 변동성 기반으로 동적 조정",
                "category": "risk",
                "priority": 0,
                "status": "pending",
                "cooldown_until": "",
            },
            {
                "id": "optimize_time_filters",
                "name": "매매 시간대 최적화",
                "description": "시간대별 승률 분석으로 나쁜 시간 자동 차단, 좋은 시간 집중 매매",
                "category": "timing",
                "priority": 0,
                "status": "pending",
                "cooldown_until": "",
            },
            {
                "id": "optimize_indicator_weights",
                "name": "기술 지표 가중치 진화",
                "description": "RSI/MACD/볼린저 등 각 지표의 기여도를 실적 기반으로 재배분",
                "category": "indicator",
                "priority": 0,
                "status": "pending",
                "cooldown_until": "",
            },
            {
                "id": "optimize_strategy_blend",
                "name": "전략 블렌딩 최적화",
                "description": "여러 전략의 신호를 앙상블하여 단일 전략 대비 안정성 향상",
                "category": "strategy",
                "priority": 0,
                "status": "pending",
                "cooldown_until": "",
            },
            {
                "id": "optimize_position_sizing",
                "name": "포지션 사이징 진화",
                "description": "신호 강도, 변동성, 승률에 따른 동적 포지션 크기 조절",
                "category": "risk",
                "priority": 0,
                "status": "pending",
                "cooldown_until": "",
            },
            {
                "id": "optimize_stock_selection",
                "name": "종목 선정 기준 진화",
                "description": "과거 수익/손실 종목 특성 분석으로 선정 기준 자동 개선",
                "category": "strategy",
                "priority": 0,
                "status": "pending",
                "cooldown_until": "",
            },
            {
                "id": "generate_custom_rules",
                "name": "맞춤형 매매 규칙 자동 생성",
                "description": "거래 패턴에서 새로운 매매 규칙을 발견하고 코드로 생성",
                "category": "feature",
                "priority": 0,
                "status": "pending",
                "cooldown_until": "",
            },
            {
                "id": "optimize_regime_adaptation",
                "name": "시장 레짐 적응 진화",
                "description": "상승/하락/횡보/급변 장세별 최적 전략 자동 전환 고도화",
                "category": "strategy",
                "priority": 0,
                "status": "pending",
                "cooldown_until": "",
            },
        ]
        self.state.roadmap = modules
        self._save_state()
        logger.info("개발 로드맵 초기화: %d개 진화 모듈 등록", len(modules))

    def _update_roadmap_priorities(self, diagnosis: PerformanceDiagnosis):
        """진단 결과 기반으로 로드맵 우선순위를 자동 갱신한다."""
        severity_score = {"critical": 30, "high": 20, "medium": 10, "low": 5}

        # 모든 모듈 우선순위 초기화
        module_scores = defaultdict(float)
        for w in diagnosis.weaknesses:
            module_id = w.get("suggested_module", "")
            sev = severity_score.get(w.get("severity", "low"), 5)
            module_scores[module_id] += sev

        now = datetime.now()
        for module in self.state.roadmap:
            mid = module["id"]
            # 약점 기반 점수
            base_score = module_scores.get(mid, 0)

            # 최근 실행 여부 감쇠 (최근 실행했으면 우선순위 낮춤)
            cooldown = module.get("cooldown_until", "")
            if cooldown:
                try:
                    cd_time = datetime.strptime(cooldown, "%Y-%m-%d %H:%M")
                    if now < cd_time:
                        module["status"] = "cooldown"
                        base_score = 0
                    else:
                        module["status"] = "pending"
                except ValueError:
                    pass

            # 한번도 실행 안 된 모듈 보너스
            applied_ids = [a["module_id"] for a in self.state.applied_modules]
            if mid not in applied_ids:
                base_score += 5

            module["priority"] = round(base_score, 1)

        logger.info("[로드맵] 우선순위 갱신 완료 — 약점 %d개 반영", len(diagnosis.weaknesses))

    # ──────────────────────────────────────────────
    # 3단계: 진화 모듈 실행
    # ──────────────────────────────────────────────

    def _execute_module(self, module_def: dict, sells: list[dict],
                        candles: list[dict] | None) -> ModuleResult:
        """진화 모듈을 실행한다."""
        mid = module_def["id"]
        logger.info("[진화 모듈] %s 실행 시작", module_def["name"])

        handlers = {
            "optimize_entry_signals": self._evolve_entry_signals,
            "optimize_exit_timing": self._evolve_exit_timing,
            "optimize_risk_profile": self._evolve_risk_profile,
            "optimize_time_filters": self._evolve_time_filters,
            "optimize_indicator_weights": self._evolve_indicator_weights,
            "optimize_strategy_blend": self._evolve_strategy_blend,
            "optimize_position_sizing": self._evolve_position_sizing,
            "optimize_stock_selection": self._evolve_stock_selection,
            "generate_custom_rules": self._evolve_custom_rules,
            "optimize_regime_adaptation": self._evolve_regime_adaptation,
        }

        handler = handlers.get(mid)
        if not handler:
            return ModuleResult(module_id=mid, reason=f"핸들러 없음: {mid}")

        try:
            result = handler(sells, candles)
            if result.success:
                logger.info("[진화 모듈] %s 성공: %s", module_def["name"], result.reason)
            else:
                logger.info("[진화 모듈] %s 스킵: %s", module_def["name"], result.reason)
            return result
        except Exception as e:
            logger.error("[진화 모듈] %s 실패: %s", module_def["name"], e)
            return ModuleResult(module_id=mid, reason=f"오류: {e}")

    def _apply_module_result(self, module_def: dict, result: ModuleResult):
        """모듈 실행 결과를 적용한다."""
        # 이전 설정 백업
        self.state.previous_config = json.loads(json.dumps(self.state.active_config))

        # 설정 병합
        for key, value in result.config_changes.items():
            if key in self.state.active_config:
                if isinstance(value, dict) and isinstance(self.state.active_config[key], dict):
                    self.state.active_config[key].update(value)
                else:
                    self.state.active_config[key] = value

        # 이력 기록
        self.state.applied_modules.append({
            "module_id": result.module_id,
            "cycle": self.state.cycle,
            "config_changes": result.config_changes,
            "reason": result.reason,
            "estimated_impact": result.estimated_impact,
            "applied_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        })
        if len(self.state.applied_modules) > 50:
            self.state.applied_modules = self.state.applied_modules[-50:]

        # 쿨다운 설정 (같은 모듈 연속 실행 방지)
        cooldown_time = (datetime.now() + timedelta(hours=6)).strftime("%Y-%m-%d %H:%M")
        module_def["cooldown_until"] = cooldown_time
        module_def["status"] = "cooldown"

        self.state.total_improvements += 1
        logger.info("[진화] 모듈 적용 완료: %s (총 %d건 개선)",
                     result.module_id, self.state.total_improvements)

    # ──────────────────────────────────────────────
    # 4단계: 자동 롤백
    # ──────────────────────────────────────────────

    def _should_rollback(self) -> bool:
        """성능 악화 시 롤백이 필요한지 판단한다."""
        history = self.state.performance_history
        if len(history) < 3:
            return False

        # 최근 3번의 성능 추이 확인
        recent = history[-3:]
        scores = [h["score"] for h in recent]

        # 연속 하락 체크
        if all(scores[i] > scores[i + 1] for i in range(len(scores) - 1)):
            drop = scores[0] - scores[-1]
            if drop > abs(self.ROLLBACK_THRESHOLD):
                logger.warning(
                    "[롤백 감지] 점수 연속 하락: %.1f → %.1f (하락폭 %.1f)",
                    scores[0], scores[-1], drop,
                )
                return True

        return False

    def _rollback(self):
        """이전 설정으로 롤백한다."""
        if self.state.previous_config:
            self.state.active_config = json.loads(json.dumps(self.state.previous_config))
            self.state.total_rollbacks += 1
            self._save_evolved_config()
            logger.warning("[롤백] 이전 설정 복원 완료 (총 %d회 롤백)", self.state.total_rollbacks)
        else:
            logger.warning("[롤백] 이전 설정 없음 — 기본값 유지")

    # ══════════════════════════════════════════════════
    # 진화 모듈 구현
    # ══════════════════════════════════════════════════

    def _evolve_entry_signals(self, sells: list[dict],
                               candles: list[dict] | None) -> ModuleResult:
        """매수 진입 신호를 최적화한다.

        분석:
        - 수익 거래 vs 손실 거래의 진입 조건 비교
        - 어떤 사유(reason)로 산 것이 잘 되었는지
        - 최적 매수 임계값 탐색
        """
        result = ModuleResult(module_id="optimize_entry_signals")

        # 사유별 승률 분석
        reason_stats = defaultdict(lambda: {"wins": 0, "losses": 0, "profit": 0})
        for t in sells:
            reason = t.get("reason", "")
            pl = t.get("profit_loss", 0)
            for kw in ["기술분석", "골든크로스", "볼린저", "RSI", "모멘텀", "패턴", "뉴스", "상승여력"]:
                if kw in reason:
                    reason_stats[kw]["profit"] += pl
                    if pl > 0:
                        reason_stats[kw]["wins"] += 1
                    else:
                        reason_stats[kw]["losses"] += 1

        # 승률 기반 가중치 조정
        adjustments = {}
        for kw, stats in reason_stats.items():
            total = stats["wins"] + stats["losses"]
            if total < 3:
                continue
            wr = stats["wins"] / total

            if wr > 0.6:
                adjustments[kw] = {"action": "boost", "weight_adj": +0.05,
                                    "reason": f"승률 {wr*100:.0f}%"}
            elif wr < 0.35:
                adjustments[kw] = {"action": "reduce", "weight_adj": -0.05,
                                    "reason": f"승률 {wr*100:.0f}%"}

        if not adjustments:
            result.reason = "진입 신호 조정 불필요 (모든 지표 정상 범위)"
            return result

        # 매수 임계값 조정
        total_wr = sum(1 for t in sells if t.get("profit_loss", 0) > 0) / len(sells)
        threshold_adj = 0
        if total_wr < 0.4:
            threshold_adj = 0.05  # 승률 낮으면 기준 상향 (더 까다롭게)
        elif total_wr > 0.65:
            threshold_adj = -0.03  # 승률 높으면 기준 하향 (더 적극적)

        result.success = True
        result.config_changes = {
            "entry_signals": {
                "reason_adjustments": adjustments,
                "buy_threshold_adj": threshold_adj,
                "evolved_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            }
        }
        result.reason = f"진입 신호 {len(adjustments)}개 조정, 임계값 {threshold_adj:+.2f}"
        result.estimated_impact = len(adjustments) * 2
        return result

    def _evolve_exit_timing(self, sells: list[dict],
                             candles: list[dict] | None) -> ModuleResult:
        """매도 타이밍을 최적화한다."""
        result = ModuleResult(module_id="optimize_exit_timing")

        # 매도 사유별 수익률 분석
        exit_stats = defaultdict(list)
        for t in sells:
            reason = t.get("reason", "")
            rate = t.get("profit_rate", 0)
            if "트레일링" in reason:
                exit_stats["trailing"].append(rate)
            elif "목표가" in reason:
                exit_stats["target"].append(rate)
            elif "손절" in reason:
                exit_stats["stop_loss"].append(rate)
            elif "장마감" in reason:
                exit_stats["market_close"].append(rate)
            elif "익절" in reason:
                exit_stats["take_profit"].append(rate)

        changes = {}

        # 트레일링스탑 최적화
        if exit_stats["trailing"]:
            avg_trail = sum(exit_stats["trailing"]) / len(exit_stats["trailing"])
            if avg_trail < 1.5 and len(exit_stats["trailing"]) >= 3:
                changes["trailing_base_adj"] = +0.5
                changes["trailing_reason"] = f"평균 {avg_trail:.1f}% → 여유 확대"
            elif avg_trail > 8.0:
                changes["trailing_base_adj"] = -0.3
                changes["trailing_reason"] = f"평균 {avg_trail:.1f}% → 약간 축소"

        # 목표가 정확도
        if exit_stats["target"]:
            avg_target = sum(exit_stats["target"]) / len(exit_stats["target"])
            target_count = len(exit_stats["target"])
            changes["target_accuracy"] = {
                "avg_profit_at_target": round(avg_target, 2),
                "target_hit_count": target_count,
            }

        # 손절 분석
        if exit_stats["stop_loss"]:
            avg_stop = sum(exit_stats["stop_loss"]) / len(exit_stats["stop_loss"])
            stop_count = len(exit_stats["stop_loss"])
            if stop_count > len(sells) * 0.25:
                changes["stop_loss_adj"] = -0.5  # 손절 너무 자주 → 넓히기
                changes["stop_reason"] = f"손절 {stop_count}건/{len(sells)}건 → 폭 확대"

        if not changes:
            result.reason = "매도 타이밍 최적화 불필요"
            return result

        result.success = True
        result.config_changes = {
            "exit_timing": {
                **changes,
                "evolved_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            }
        }
        result.reason = f"매도 타이밍 {len(changes)}개 파라미터 조정"
        result.estimated_impact = len(changes) * 1.5
        return result

    def _evolve_risk_profile(self, sells: list[dict],
                              candles: list[dict] | None) -> ModuleResult:
        """리스크 프로필을 진화시킨다."""
        result = ModuleResult(module_id="optimize_risk_profile")

        profits = [t.get("profit_loss", 0) for t in sells]
        rates = [t.get("profit_rate", 0) for t in sells]

        wins = [p for p in profits if p > 0]
        losses = [p for p in profits if p < 0]
        if not wins or not losses:
            result.reason = "승/패 데이터 부족"
            return result

        avg_win = sum(wins) / len(wins)
        avg_loss = sum(losses) / len(losses)
        max_loss = min(rates)
        max_win = max(rates)

        changes = {}

        # 손절 최적화: 평균 손실의 1.5배를 기준으로
        optimal_stop = max(-8.0, min(-1.5, min(rates) * 0.7))
        current_stop = self.state.active_config.get("risk_profile", {}).get("stop_loss_pct", -3.0)
        if abs(optimal_stop - current_stop) > 0.5:
            changes["stop_loss_pct"] = round(optimal_stop, 1)

        # 익절 최적화: 상위 25% 수익률 기준
        win_rates = sorted([r for r in rates if r > 0])
        if len(win_rates) >= 4:
            p75 = win_rates[int(len(win_rates) * 0.75)]
            optimal_take = max(10.0, min(40.0, p75 * 1.2))
            changes["take_profit_pct"] = round(optimal_take, 1)

        # 최대 손실 한도
        changes["max_daily_loss"] = round(abs(avg_loss) * 5, 0)

        if not changes:
            result.reason = "리스크 프로필 변경 불필요"
            return result

        result.success = True
        result.config_changes = {
            "risk_profile": {
                **changes,
                "evolved_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            }
        }
        result.reason = f"리스크 파라미터 {len(changes)}개 최적화"
        result.estimated_impact = 5
        return result

    def _evolve_time_filters(self, sells: list[dict],
                              candles: list[dict] | None) -> ModuleResult:
        """매매 시간대를 최적화한다."""
        result = ModuleResult(module_id="optimize_time_filters")

        by_hour = defaultdict(lambda: {"wins": 0, "losses": 0, "profit": 0})
        for t in sells:
            ts = t.get("timestamp", "")
            if len(ts) < 13:
                continue
            hour = ts[11:13]
            pl = t.get("profit_loss", 0)
            by_hour[hour]["profit"] += pl
            if pl > 0:
                by_hour[hour]["wins"] += 1
            else:
                by_hour[hour]["losses"] += 1

        blocked = []
        preferred = []
        for hour, stats in sorted(by_hour.items()):
            total = stats["wins"] + stats["losses"]
            if total < 3:
                continue
            wr = stats["wins"] / total * 100
            avg = stats["profit"] / total

            if wr < 30 and total >= 5:
                blocked.append({"hour": hour, "win_rate": round(wr, 1),
                                "avg_profit": round(avg, 0), "trades": total})
            elif wr > 65 and total >= 5:
                preferred.append({"hour": hour, "win_rate": round(wr, 1),
                                  "avg_profit": round(avg, 0), "trades": total})

        if not blocked and not preferred:
            result.reason = "시간대 필터 조정 불필요"
            return result

        result.success = True
        result.config_changes = {
            "time_filters": {
                "blocked_hours": blocked,
                "preferred_hours": preferred,
                "evolved_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            }
        }
        blocked_str = ",".join(h["hour"] + "시" for h in blocked)
        preferred_str = ",".join(h["hour"] + "시" for h in preferred)
        result.reason = f"차단: [{blocked_str}] / 선호: [{preferred_str}]"
        result.estimated_impact = (len(blocked) + len(preferred)) * 3
        return result

    def _evolve_indicator_weights(self, sells: list[dict],
                                   candles: list[dict] | None) -> ModuleResult:
        """기술 지표 가중치를 성과 기반으로 진화시킨다."""
        result = ModuleResult(module_id="optimize_indicator_weights")

        # 사유에서 지표 키워드 추출 → 성과 집계
        indicator_map = {
            "RSI": "rsi", "MACD": "macd", "볼린저": "bollinger",
            "골든크로스": "golden_cross", "데드크로스": "dead_cross",
            "모멘텀": "momentum", "패턴": "pattern", "뉴스": "sentiment",
            "상승여력": "upside", "기술분석": "technical",
        }

        stats = {}
        for t in sells:
            reason = t.get("reason", "")
            pl = t.get("profit_loss", 0)
            for keyword, ind_key in indicator_map.items():
                if keyword in reason:
                    if ind_key not in stats:
                        stats[ind_key] = {"wins": 0, "losses": 0, "total_profit": 0}
                    stats[ind_key]["total_profit"] += pl
                    if pl > 0:
                        stats[ind_key]["wins"] += 1
                    else:
                        stats[ind_key]["losses"] += 1

        weights = {}
        for ind, s in stats.items():
            total = s["wins"] + s["losses"]
            if total < 3:
                continue
            wr = s["wins"] / total
            avg_profit = s["total_profit"] / total

            # 가중치 = 승률 × 수익성의 조합
            score = wr * 0.6 + min(max(avg_profit / 10000, -1), 1) * 0.4
            weights[ind] = {
                "score": round(score, 3),
                "win_rate": round(wr * 100, 1),
                "avg_profit": round(avg_profit, 0),
                "trades": total,
                "recommended_weight": round(max(0.05, min(0.4, score * 0.5)), 2),
            }

        if not weights:
            result.reason = "지표 가중치 데이터 부족"
            return result

        result.success = True
        result.config_changes = {
            "indicator_weights": {
                **weights,
                "evolved_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            }
        }
        result.reason = f"{len(weights)}개 지표 가중치 재배분"
        result.estimated_impact = len(weights) * 2
        return result

    def _evolve_strategy_blend(self, sells: list[dict],
                                candles: list[dict] | None) -> ModuleResult:
        """전략 블렌딩을 최적화한다."""
        result = ModuleResult(module_id="optimize_strategy_blend")

        # 최근 거래 성과 추세 분석
        recent = sells[-20:] if len(sells) > 20 else sells
        first_half = recent[:len(recent)//2]
        second_half = recent[len(recent)//2:]

        if not first_half or not second_half:
            result.reason = "데이터 부족"
            return result

        wr1 = sum(1 for t in first_half if t.get("profit_loss", 0) > 0) / len(first_half) * 100
        wr2 = sum(1 for t in second_half if t.get("profit_loss", 0) > 0) / len(second_half) * 100

        blend_config = {}

        # 성과 추세에 따른 전략 모드 결정
        if wr2 > wr1 + 10:
            # 개선 추세 → 현재 방향 유지, 약간 공격적
            blend_config["mode"] = "aggressive"
            blend_config["buy_threshold_adj"] = -0.03
            blend_config["reason"] = f"승률 상승 추세 ({wr1:.0f}%→{wr2:.0f}%)"
        elif wr2 < wr1 - 10:
            # 악화 추세 → 방어적
            blend_config["mode"] = "defensive"
            blend_config["buy_threshold_adj"] = +0.05
            blend_config["reason"] = f"승률 하락 추세 ({wr1:.0f}%→{wr2:.0f}%)"
        else:
            blend_config["mode"] = "balanced"
            blend_config["reason"] = f"승률 안정 ({wr1:.0f}%→{wr2:.0f}%)"

        # 손익비 기반 전략 조정
        win_profits = [t.get("profit_loss", 0) for t in recent if t.get("profit_loss", 0) > 0]
        loss_profits = [t.get("profit_loss", 0) for t in recent if t.get("profit_loss", 0) < 0]
        if win_profits and loss_profits:
            avg_w = sum(win_profits) / len(win_profits)
            avg_l = abs(sum(loss_profits) / len(loss_profits))
            if avg_l > avg_w * 1.5:
                blend_config["focus"] = "cut_losses"
                blend_config["stop_loss_tighter"] = True
            elif avg_w > avg_l * 2:
                blend_config["focus"] = "let_winners_run"
                blend_config["trailing_wider"] = True

        result.success = True
        result.config_changes = {"strategy_blend": blend_config}
        result.reason = blend_config.get("reason", "전략 블렌딩 조정")
        result.estimated_impact = 3
        return result

    def _evolve_position_sizing(self, sells: list[dict],
                                 candles: list[dict] | None) -> ModuleResult:
        """포지션 사이징을 진화시킨다."""
        result = ModuleResult(module_id="optimize_position_sizing")

        # 신호 강도별 수익 분석 (BUY 거래의 amount로 추정)
        amounts = [t.get("amount", 0) for t in sells if t.get("amount", 0) > 0]
        profits = [t.get("profit_loss", 0) for t in sells]

        if len(amounts) < 5:
            result.reason = "포지션 데이터 부족"
            return result

        # 큰 포지션 vs 작은 포지션 성과 비교
        median_amt = sorted(amounts)[len(amounts) // 2]
        large = [(a, p) for a, p in zip(amounts, profits) if a > median_amt]
        small = [(a, p) for a, p in zip(amounts, profits) if a <= median_amt]

        sizing_config = {}
        if large and small:
            large_wr = sum(1 for _, p in large if p > 0) / len(large) * 100
            small_wr = sum(1 for _, p in small if p > 0) / len(small) * 100

            if small_wr > large_wr + 10:
                sizing_config["recommendation"] = "reduce_size"
                sizing_config["size_multiplier"] = 0.8
                sizing_config["reason"] = f"소형 포지션 승률({small_wr:.0f}%) > 대형({large_wr:.0f}%)"
            elif large_wr > small_wr + 10:
                sizing_config["recommendation"] = "increase_size"
                sizing_config["size_multiplier"] = 1.2
                sizing_config["reason"] = f"대형 포지션 승률({large_wr:.0f}%) > 소형({small_wr:.0f}%)"
            else:
                result.reason = "포지션 사이징 변경 불필요"
                return result

        if not sizing_config:
            result.reason = "포지션 사이징 분석 불충분"
            return result

        result.success = True
        result.config_changes = {"position_sizing": sizing_config}
        result.reason = sizing_config.get("reason", "포지션 사이징 최적화")
        result.estimated_impact = 2
        return result

    def _evolve_stock_selection(self, sells: list[dict],
                                 candles: list[dict] | None) -> ModuleResult:
        """종목 선정 기준을 진화시킨다."""
        result = ModuleResult(module_id="optimize_stock_selection")

        # 종목별 성과 집계
        stock_stats = defaultdict(lambda: {"wins": 0, "losses": 0, "profit": 0})
        for t in sells:
            code = t.get("stock_code", "")
            name = t.get("stock_name", "")
            pl = t.get("profit_loss", 0)
            key = f"{code}:{name}"
            stock_stats[key]["profit"] += pl
            if pl > 0:
                stock_stats[key]["wins"] += 1
            else:
                stock_stats[key]["losses"] += 1

        # 반복 손실 종목 식별
        bad_stocks = []
        good_stocks = []
        for key, stats in stock_stats.items():
            total = stats["wins"] + stats["losses"]
            if total < 2:
                continue
            wr = stats["wins"] / total * 100
            if wr < 30 and stats["profit"] < 0:
                bad_stocks.append({"stock": key, "win_rate": wr,
                                    "total_profit": stats["profit"], "trades": total})
            elif wr > 70 and stats["profit"] > 0:
                good_stocks.append({"stock": key, "win_rate": wr,
                                     "total_profit": stats["profit"], "trades": total})

        # 가격대별 분석
        price_stats = defaultdict(lambda: {"wins": 0, "losses": 0})
        for t in sells:
            price = t.get("price", 0)
            if price < 5000:
                bucket = "under_5k"
            elif price < 20000:
                bucket = "5k_20k"
            elif price < 50000:
                bucket = "20k_50k"
            elif price < 100000:
                bucket = "50k_100k"
            else:
                bucket = "over_100k"
            if t.get("profit_loss", 0) > 0:
                price_stats[bucket]["wins"] += 1
            else:
                price_stats[bucket]["losses"] += 1

        best_bucket = None
        best_wr = 0
        for bucket, stats in price_stats.items():
            total = stats["wins"] + stats["losses"]
            if total >= 3:
                wr = stats["wins"] / total * 100
                if wr > best_wr:
                    best_wr = wr
                    best_bucket = bucket

        selection_config = {
            "bad_stocks": bad_stocks[:10],
            "good_stocks": good_stocks[:10],
            "best_price_range": best_bucket,
            "best_price_wr": round(best_wr, 1),
        }

        if not bad_stocks and not good_stocks:
            result.reason = "종목 선정 패턴 분석 불충분"
            return result

        result.success = True
        result.config_changes = {"stock_filters": selection_config}
        result.reason = f"회피 종목 {len(bad_stocks)}개, 선호 종목 {len(good_stocks)}개 식별"
        result.estimated_impact = len(bad_stocks) * 2
        return result

    def _evolve_custom_rules(self, sells: list[dict],
                              candles: list[dict] | None) -> ModuleResult:
        """맞춤형 매매 규칙을 자동 생성한다.

        패턴 분석으로 새로운 규칙을 발견하고 코드화한다.
        """
        result = ModuleResult(module_id="generate_custom_rules")

        # 연속 패턴 분석
        rules = []

        # 규칙 1: 연속 손실 후 다음 거래 패턴
        streak = 0
        after_streak = []
        for i, t in enumerate(sells):
            if t.get("profit_loss", 0) < 0:
                streak += 1
            else:
                if streak >= 3 and i < len(sells):
                    after_streak.append(t.get("profit_loss", 0))
                streak = 0

        if len(after_streak) >= 3:
            after_wr = sum(1 for p in after_streak if p > 0) / len(after_streak) * 100
            if after_wr < 35:
                rules.append({
                    "id": "pause_after_streak",
                    "type": "risk_control",
                    "condition": "3연패 후",
                    "action": "다음 1건 매수 스킵",
                    "confidence": min(0.8, len(after_streak) / 5),
                    "evidence": f"3연패 후 승률 {after_wr:.0f}% ({len(after_streak)}건)",
                })
            elif after_wr > 65:
                rules.append({
                    "id": "aggressive_after_streak",
                    "type": "entry",
                    "condition": "3연패 후",
                    "action": "포지션 사이즈 +20%",
                    "confidence": min(0.8, len(after_streak) / 5),
                    "evidence": f"3연패 후 승률 {after_wr:.0f}% ({len(after_streak)}건)",
                })

        # 규칙 2: 요일별 패턴
        day_stats = defaultdict(lambda: {"wins": 0, "losses": 0})
        for t in sells:
            ts = t.get("timestamp", "")
            if len(ts) >= 10:
                try:
                    day = datetime.strptime(ts[:10], "%Y-%m-%d").weekday()
                    if t.get("profit_loss", 0) > 0:
                        day_stats[day]["wins"] += 1
                    else:
                        day_stats[day]["losses"] += 1
                except ValueError:
                    pass

        day_names = {0: "월", 1: "화", 2: "수", 3: "목", 4: "금"}
        for day, stats in day_stats.items():
            total = stats["wins"] + stats["losses"]
            if total < 5:
                continue
            wr = stats["wins"] / total * 100
            if wr < 30:
                rules.append({
                    "id": f"avoid_day_{day}",
                    "type": "time_filter",
                    "condition": f"{day_names.get(day, str(day))}요일",
                    "action": "매수 포지션 축소 50%",
                    "confidence": min(0.7, total / 10),
                    "evidence": f"{day_names.get(day)}요일 승률 {wr:.0f}% ({total}건)",
                })
            elif wr > 70:
                rules.append({
                    "id": f"prefer_day_{day}",
                    "type": "time_filter",
                    "condition": f"{day_names.get(day, str(day))}요일",
                    "action": "매수 임계값 -0.05 (적극 매수)",
                    "confidence": min(0.7, total / 10),
                    "evidence": f"{day_names.get(day)}요일 승률 {wr:.0f}% ({total}건)",
                })

        # 규칙 3: 수익률 구간별 보유 시간 최적화
        win_rates_sorted = sorted(
            [t.get("profit_rate", 0) for t in sells if t.get("profit_loss", 0) > 0])
        if len(win_rates_sorted) >= 5:
            median_rate = win_rates_sorted[len(win_rates_sorted) // 2]
            if median_rate < 2.0:
                rules.append({
                    "id": "quick_exit",
                    "type": "exit",
                    "condition": f"수익 중앙값 {median_rate:.1f}%",
                    "action": "2% 도달 시 50% 부분매도 고려",
                    "confidence": 0.5,
                    "evidence": f"수익 중앙값이 낮음 → 빠른 이익 확보 권장",
                })

        if not rules:
            result.reason = "새로운 규칙 패턴 발견 안 됨"
            return result

        # 기존 규칙과 중복 제거
        existing_ids = {r["id"] for r in self.state.active_config.get("custom_rules", [])}
        new_rules = [r for r in rules if r["id"] not in existing_ids]

        if not new_rules:
            result.reason = "신규 규칙 없음 (기존 규칙과 중복)"
            return result

        result.success = True
        result.config_changes = {
            "custom_rules": self.state.active_config.get("custom_rules", []) + new_rules,
        }
        result.reason = f"{len(new_rules)}개 새 규칙 생성"
        result.estimated_impact = len(new_rules) * 3
        return result

    def _evolve_regime_adaptation(self, sells: list[dict],
                                    candles: list[dict] | None) -> ModuleResult:
        """시장 레짐 적응을 진화시킨다."""
        result = ModuleResult(module_id="optimize_regime_adaptation")

        # 최근 거래의 수익률 변동성으로 레짐 추정
        rates = [t.get("profit_rate", 0) for t in sells[-20:]]
        if len(rates) < 10:
            result.reason = "레짐 분석 데이터 부족"
            return result

        mean_rate = sum(rates) / len(rates)
        variance = sum((r - mean_rate) ** 2 for r in rates) / len(rates)
        volatility = variance ** 0.5

        regime_config = {}

        if volatility > 3.0:
            # 고변동성 → 방어 모드
            regime_config["current_regime"] = "volatile"
            regime_config["recommended_mode"] = "defensive"
            regime_config["position_size_mult"] = 0.6
            regime_config["buy_threshold_adj"] = +0.08
        elif mean_rate > 1.0 and volatility < 2.0:
            # 안정적 수익 → 공격 모드
            regime_config["current_regime"] = "trending_up"
            regime_config["recommended_mode"] = "aggressive"
            regime_config["position_size_mult"] = 1.2
            regime_config["buy_threshold_adj"] = -0.05
        elif mean_rate < -0.5:
            # 손실 추세 → 초방어 모드
            regime_config["current_regime"] = "trending_down"
            regime_config["recommended_mode"] = "ultra_defensive"
            regime_config["position_size_mult"] = 0.4
            regime_config["buy_threshold_adj"] = +0.12
        else:
            regime_config["current_regime"] = "ranging"
            regime_config["recommended_mode"] = "balanced"
            regime_config["position_size_mult"] = 0.9
            regime_config["buy_threshold_adj"] = 0

        regime_config["volatility"] = round(volatility, 2)
        regime_config["mean_rate"] = round(mean_rate, 2)

        result.success = True
        result.config_changes = {"regime_strategies": regime_config}
        result.reason = (f"레짐={regime_config['current_regime']} "
                         f"모드={regime_config['recommended_mode']} "
                         f"변동성={volatility:.1f}")
        result.estimated_impact = 4
        return result

    # ──────────────────────────────────────────────
    # 영속성
    # ──────────────────────────────────────────────

    def _load_state(self) -> CodeEvolutionState:
        """저장된 상태를 로드한다."""
        if CODE_EVO_STATE_FILE.exists():
            try:
                data = json.loads(CODE_EVO_STATE_FILE.read_text(encoding="utf-8"))
                state = CodeEvolutionState(**{
                    k: v for k, v in data.items()
                    if k in CodeEvolutionState.__dataclass_fields__
                })
                logger.info(
                    "코드 진화 상태 복원: 사이클 #%d, 개선 %d건",
                    state.cycle, state.total_improvements,
                )
                return state
            except (json.JSONDecodeError, TypeError, OSError) as e:
                logger.warning("코드 진화 상태 로드 실패: %s", e)
        return CodeEvolutionState()

    def _save_state(self):
        """현재 상태를 저장한다."""
        CODE_EVO_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        CODE_EVO_STATE_FILE.write_text(
            json.dumps(asdict(self.state), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _save_evolved_config(self):
        """진화된 설정을 별도 파일로 저장한다.

        이 파일은 전략/트레이더가 읽어서 적용한다.
        """
        EVOLVED_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        config = {
            **self.state.active_config,
            "_meta": {
                "cycle": self.state.cycle,
                "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "total_improvements": self.state.total_improvements,
            }
        }
        EVOLVED_CONFIG_FILE.write_text(
            json.dumps(config, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    # ══════════════════════════════════════════════════
    # 자동 매매 복기 엔진 (v3.2)
    # ══════════════════════════════════════════════════

    REVIEW_FILE = Path("data/trade_reviews.json")

    def _deep_review_trades(self, sells: list[dict]) -> dict:
        """거래를 심층 복기하여 구체적 개선사항을 도출한다.

        기존 _diagnose_performance()가 수치 진단이라면,
        이 메서드는 패턴/맥락 기반 질적 분석을 수행한다.

        분석 항목:
        1. 보유시간별 성과 (단타 vs 스윙)
        2. 매수 사유 키워드별 상세 분석
        3. 연속 패턴 (승→패, 패→승 전환)
        4. 가격대별 성과
        5. 수익/손실 크기 분포 분석

        Returns:
            {findings, additional_weaknesses, report, auto_applied}
        """
        if len(sells) < 5:
            return {"findings": [], "additional_weaknesses": [], "report": "거래 데이터 부족", "auto_applied": 0}

        findings = []
        additional_weaknesses = []
        auto_rules = []

        # ── 1. 보유시간별 성과 분석 ──
        holding_analysis = self._analyze_holding_periods(sells)
        if holding_analysis:
            findings.append(holding_analysis)
            if holding_analysis.get("severity") == "high":
                additional_weaknesses.append(holding_analysis)

        # ── 2. 매수 사유 키워드별 심층 분석 ──
        reason_analysis = self._analyze_reason_patterns(sells)
        findings.extend(reason_analysis)
        for r in reason_analysis:
            if r.get("severity") in ("high", "critical"):
                additional_weaknesses.append(r)

        # ── 3. 연속 패턴 분석 (승패 전환) ──
        streak_analysis = self._analyze_streaks(sells)
        if streak_analysis:
            findings.append(streak_analysis)

        # ── 4. 가격대별 성과 ──
        price_analysis = self._analyze_price_ranges(sells)
        if price_analysis:
            findings.append(price_analysis)
            if price_analysis.get("severity") in ("high", "critical"):
                additional_weaknesses.append(price_analysis)

        # ── 5. 수익/손실 크기 분포 ──
        distribution = self._analyze_profit_distribution(sells)
        if distribution:
            findings.append(distribution)

        # ── 6. 자연어 복기 리포트 생성 ──
        report = self._generate_review_report(sells, findings)

        # ── 7. 발견사항 → 자동 규칙 변환 ──
        auto_applied = self._auto_apply_findings(findings)

        # ── 8. 복기 결과 저장 ──
        review_result = {
            "findings": findings,
            "additional_weaknesses": additional_weaknesses,
            "report": report,
            "auto_applied": auto_applied,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        self._save_review(review_result)

        logger.info(
            "매매 복기 완료: 발견사항 %d개, 약점 %d개, 자동적용 %d건",
            len(findings), len(additional_weaknesses), auto_applied,
        )
        return review_result

    def _analyze_holding_periods(self, sells: list[dict]) -> dict | None:
        """보유시간별 성과를 분석한다."""
        # BUY-SELL 쌍을 매칭하여 보유시간 추정
        buys = {}
        pairs = []

        # trades.json에서 전체 거래 로드
        all_trades = []
        if TRADES_FILE.exists():
            try:
                all_trades = json.loads(TRADES_FILE.read_text(encoding="utf-8"))
            except Exception:
                pass

        for t in all_trades:
            code = t.get("stock_code", "")
            if t.get("side") == "BUY":
                buys[code] = t.get("timestamp", "")
            elif t.get("side") == "SELL" and code in buys:
                buy_ts = buys.pop(code)
                pairs.append({
                    "buy_time": buy_ts,
                    "sell_time": t.get("timestamp", ""),
                    "profit_rate": t.get("profit_rate", 0),
                    "profit_loss": t.get("profit_loss", 0),
                })

        if len(pairs) < 5:
            return None

        # 보유시간 추정 (같은 날짜면 분 차이, 다른 날짜면 일 차이)
        short_term = []  # 당일 매매
        swing = []       # 1일 이상

        for p in pairs:
            buy_date = p["buy_time"][:10] if len(p["buy_time"]) >= 10 else ""
            sell_date = p["sell_time"][:10] if len(p["sell_time"]) >= 10 else ""

            if buy_date == sell_date:
                short_term.append(p["profit_rate"])
            else:
                swing.append(p["profit_rate"])

        result = {"area": "holding_period", "detail": ""}

        if short_term and swing:
            st_wr = sum(1 for r in short_term if r > 0) / len(short_term) * 100
            sw_wr = sum(1 for r in swing if r > 0) / len(swing) * 100
            st_avg = sum(short_term) / len(short_term)
            sw_avg = sum(swing) / len(swing)

            if st_wr > sw_wr + 15:
                result["detail"] = (
                    f"단타(당일) 승률 {st_wr:.0f}% > 스윙 승률 {sw_wr:.0f}% — "
                    f"단타 평균수익 {st_avg:.2f}% vs 스윙 {sw_avg:.2f}%"
                )
                result["severity"] = "medium"
                result["recommendation"] = "단기 보유 전략 강화 권장"
            elif sw_wr > st_wr + 15:
                result["detail"] = (
                    f"스윙 승률 {sw_wr:.0f}% > 단타 승률 {st_wr:.0f}% — "
                    f"스윙 평균수익 {sw_avg:.2f}% vs 단타 {st_avg:.2f}%"
                )
                result["severity"] = "medium"
                result["recommendation"] = "보유 기간 확대 권장"
            else:
                result["detail"] = f"단타 승률 {st_wr:.0f}%, 스윙 승률 {sw_wr:.0f}% — 유의미한 차이 없음"
                result["severity"] = "low"
                result["recommendation"] = "현재 보유 전략 유지"

            result["data"] = {
                "short_term_wr": round(st_wr, 1), "swing_wr": round(sw_wr, 1),
                "short_term_avg": round(st_avg, 2), "swing_avg": round(sw_avg, 2),
                "short_term_count": len(short_term), "swing_count": len(swing),
            }
            return result

        return None

    def _analyze_reason_patterns(self, sells: list[dict]) -> list[dict]:
        """매수 사유별 상세 성과를 분석한다."""
        # 키워드 → 거래 결과 매핑
        keywords = [
            "기술분석", "가치투자", "골든크로스", "데드크로스",
            "볼린저", "RSI", "MACD", "모멘텀", "패턴", "뉴스",
            "상승여력", "추세소진", "트레일링", "손절", "목표가",
            "과매도", "반등", "돌파",
        ]

        stats = defaultdict(lambda: {"profits": [], "rates": []})
        for t in sells:
            reason = t.get("reason", "")
            pl = t.get("profit_loss", 0)
            rate = t.get("profit_rate", 0)
            for kw in keywords:
                if kw in reason:
                    stats[kw]["profits"].append(pl)
                    stats[kw]["rates"].append(rate)

        findings = []
        for kw, data in stats.items():
            if len(data["profits"]) < 3:
                continue

            total = len(data["profits"])
            wins = sum(1 for p in data["profits"] if p > 0)
            wr = wins / total * 100
            avg_rate = sum(data["rates"]) / total
            total_profit = sum(data["profits"])

            severity = "low"
            if wr < 30:
                severity = "high"
            elif wr < 40:
                severity = "medium"

            findings.append({
                "area": "reason_pattern",
                "keyword": kw,
                "detail": (
                    f"'{kw}' 관련 거래: {total}건 승률 {wr:.0f}% "
                    f"평균수익률 {avg_rate:.2f}% 총손익 {total_profit:+,.0f}원"
                ),
                "severity": severity,
                "data": {
                    "keyword": kw, "total": total, "wins": wins,
                    "win_rate": round(wr, 1), "avg_rate": round(avg_rate, 2),
                    "total_profit": total_profit,
                },
            })

        # 승률 순으로 정렬
        findings.sort(key=lambda f: f["data"].get("win_rate", 50))
        return findings

    def _analyze_streaks(self, sells: list[dict]) -> dict | None:
        """연속 승/패 패턴을 분석한다."""
        results = [1 if t.get("profit_loss", 0) > 0 else 0 for t in sells]
        if len(results) < 10:
            return None

        # 연속 패턴 통계
        max_win_streak = 0
        max_loss_streak = 0
        current = 0
        current_type = None

        win_streaks = []
        loss_streaks = []

        for r in results:
            if r == current_type:
                current += 1
            else:
                if current_type == 1 and current > 0:
                    win_streaks.append(current)
                elif current_type == 0 and current > 0:
                    loss_streaks.append(current)
                current = 1
                current_type = r

        if current_type == 1:
            win_streaks.append(current)
        elif current_type == 0:
            loss_streaks.append(current)

        max_win = max(win_streaks) if win_streaks else 0
        max_loss = max(loss_streaks) if loss_streaks else 0
        avg_win = sum(win_streaks) / len(win_streaks) if win_streaks else 0
        avg_loss = sum(loss_streaks) / len(loss_streaks) if loss_streaks else 0

        # 3연패 후 다음 거래 패턴
        after_3loss = []
        streak = 0
        for i, r in enumerate(results):
            if r == 0:
                streak += 1
            else:
                if streak >= 3 and i < len(results):
                    after_3loss.append(r)
                streak = 0

        after_3loss_wr = (sum(after_3loss) / len(after_3loss) * 100) if after_3loss else 0

        return {
            "area": "streak_pattern",
            "severity": "medium" if max_loss >= 4 else "low",
            "detail": (
                f"최대 연승 {max_win}회, 최대 연패 {max_loss}회 | "
                f"평균 연승 {avg_win:.1f}, 평균 연패 {avg_loss:.1f} | "
                f"3연패 후 승률 {after_3loss_wr:.0f}% ({len(after_3loss)}건)"
            ),
            "data": {
                "max_win_streak": max_win, "max_loss_streak": max_loss,
                "avg_win_streak": round(avg_win, 1), "avg_loss_streak": round(avg_loss, 1),
                "after_3loss_wr": round(after_3loss_wr, 1), "after_3loss_count": len(after_3loss),
            },
        }

    def _analyze_price_ranges(self, sells: list[dict]) -> dict | None:
        """가격대별 성과를 분석한다."""
        buckets = defaultdict(lambda: {"wins": 0, "losses": 0, "profit": 0})
        bucket_names = {
            "under_5k": "5천원 미만",
            "5k_20k": "5천~2만원",
            "20k_50k": "2만~5만원",
            "50k_100k": "5만~10만원",
            "over_100k": "10만원 이상",
        }

        for t in sells:
            price = t.get("price", 0)
            if price < 5000:
                bucket = "under_5k"
            elif price < 20000:
                bucket = "5k_20k"
            elif price < 50000:
                bucket = "20k_50k"
            elif price < 100000:
                bucket = "50k_100k"
            else:
                bucket = "over_100k"

            pl = t.get("profit_loss", 0)
            buckets[bucket]["profit"] += pl
            if pl > 0:
                buckets[bucket]["wins"] += 1
            else:
                buckets[bucket]["losses"] += 1

        best = None
        worst = None
        best_wr = -1
        worst_wr = 101

        details = []
        for bucket, stats in sorted(buckets.items()):
            total = stats["wins"] + stats["losses"]
            if total < 3:
                continue
            wr = stats["wins"] / total * 100
            name = bucket_names.get(bucket, bucket)
            details.append(f"{name}: 승률 {wr:.0f}%({total}건) 손익 {stats['profit']:+,.0f}원")

            if wr > best_wr:
                best_wr = wr
                best = name
            if wr < worst_wr:
                worst_wr = wr
                worst = name

        if not details:
            return None

        severity = "low"
        if worst and worst_wr < 30:
            severity = "high"
        elif worst and worst_wr < 40:
            severity = "medium"

        return {
            "area": "price_range",
            "severity": severity,
            "detail": " | ".join(details),
            "recommendation": f"최적 가격대: {best}(승률 {best_wr:.0f}%)" if best else "",
            "data": {
                "best_range": best, "best_wr": round(best_wr, 1),
                "worst_range": worst, "worst_wr": round(worst_wr, 1),
            },
        }

    def _analyze_profit_distribution(self, sells: list[dict]) -> dict | None:
        """수익/손실 크기 분포를 분석한다."""
        rates = [t.get("profit_rate", 0) for t in sells]
        if len(rates) < 5:
            return None

        wins = sorted([r for r in rates if r > 0])
        losses = sorted([r for r in rates if r < 0])

        avg_win = sum(wins) / len(wins) if wins else 0
        avg_loss = sum(losses) / len(losses) if losses else 0
        median_win = wins[len(wins) // 2] if wins else 0
        median_loss = losses[len(losses) // 2] if losses else 0
        max_win = max(wins) if wins else 0
        max_loss = min(losses) if losses else 0

        # 큰 손실이 작은 수익을 잡아먹는 패턴 확인
        severity = "low"
        if avg_loss != 0 and avg_win < abs(avg_loss) * 0.8:
            severity = "high"
        elif avg_loss != 0 and avg_win < abs(avg_loss):
            severity = "medium"

        return {
            "area": "profit_distribution",
            "severity": severity,
            "detail": (
                f"평균 수익 {avg_win:.2f}% (중앙값 {median_win:.2f}%, 최대 {max_win:.2f}%) | "
                f"평균 손실 {avg_loss:.2f}% (중앙값 {median_loss:.2f}%, 최대 {max_loss:.2f}%) | "
                f"손익비 {avg_win/abs(avg_loss):.2f}" if avg_loss != 0 else
                f"평균 수익 {avg_win:.2f}% | 손실 없음"
            ),
            "data": {
                "avg_win": round(avg_win, 2), "avg_loss": round(avg_loss, 2),
                "median_win": round(median_win, 2), "median_loss": round(median_loss, 2),
                "max_win": round(max_win, 2), "max_loss": round(max_loss, 2),
            },
        }

    def _generate_review_report(self, sells: list[dict], findings: list[dict]) -> str:
        """자연어 매매 복기 리포트를 생성한다."""
        total = len(sells)
        wins = sum(1 for t in sells if t.get("profit_loss", 0) > 0)
        total_profit = sum(t.get("profit_loss", 0) for t in sells)
        wr = wins / total * 100 if total > 0 else 0

        lines = [
            f"═══ 매매 복기 리포트 ═══",
            f"분석 기간: 최근 {total}건 매도 거래",
            f"전체 승률: {wr:.1f}% ({wins}승 {total - wins}패)",
            f"총 손익: {total_profit:+,.0f}원",
            "",
        ]

        # 핵심 발견사항
        high_findings = [f for f in findings if f.get("severity") in ("high", "critical")]
        medium_findings = [f for f in findings if f.get("severity") == "medium"]

        if high_findings:
            lines.append("▶ 긴급 개선 필요:")
            for f in high_findings:
                lines.append(f"  - {f['detail']}")
                if f.get("recommendation"):
                    lines.append(f"    → {f['recommendation']}")
            lines.append("")

        if medium_findings:
            lines.append("▶ 개선 권장:")
            for f in medium_findings:
                lines.append(f"  - {f['detail']}")
            lines.append("")

        # 전략별 성과 요약 (reason_pattern findings에서 추출)
        reason_findings = [f for f in findings if f.get("area") == "reason_pattern"]
        if reason_findings:
            lines.append("▶ 전략별 성과:")
            for f in sorted(reason_findings, key=lambda x: x["data"]["win_rate"], reverse=True):
                d = f["data"]
                emoji = "✅" if d["win_rate"] >= 60 else "⚠️" if d["win_rate"] >= 40 else "❌"
                lines.append(
                    f"  {emoji} {d['keyword']}: 승률 {d['win_rate']:.0f}% "
                    f"({d['total']}건) 평균 {d['avg_rate']:+.2f}%"
                )
            lines.append("")

        # 연속 패턴
        streak = next((f for f in findings if f.get("area") == "streak_pattern"), None)
        if streak:
            lines.append(f"▶ 연속 패턴: {streak['detail']}")
            lines.append("")

        lines.append(f"리포트 생성: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        return "\n".join(lines)

    def _auto_apply_findings(self, findings: list[dict]) -> int:
        """발견사항을 자동으로 규칙에 반영한다.

        Returns:
            적용된 규칙 수
        """
        applied = 0

        for f in findings:
            if f.get("severity") not in ("high", "critical"):
                continue

            area = f.get("area", "")
            data = f.get("data", {})

            # 가격대 약점 → stock_filters에 반영
            if area == "price_range" and data.get("worst_wr", 100) < 30:
                worst = data.get("worst_range", "")
                if worst:
                    filters = self.state.active_config.get("stock_filters", {})
                    avoid_ranges = filters.get("avoid_price_ranges", [])
                    if worst not in avoid_ranges:
                        avoid_ranges.append(worst)
                        self.state.active_config.setdefault("stock_filters", {})["avoid_price_ranges"] = avoid_ranges
                        applied += 1
                        logger.info("복기 자동적용: 가격대 '%s' 회피 (승률 %.0f%%)", worst, data["worst_wr"])

            # 사유별 약점 → entry_signals에 반영
            elif area == "reason_pattern" and data.get("win_rate", 100) < 30:
                keyword = data.get("keyword", "")
                if keyword:
                    entry = self.state.active_config.setdefault("entry_signals", {})
                    weak_signals = entry.get("weak_signals", [])
                    if keyword not in weak_signals:
                        weak_signals.append(keyword)
                        entry["weak_signals"] = weak_signals
                        applied += 1
                        logger.info("복기 자동적용: '%s' 신호 약화 (승률 %.0f%%)", keyword, data["win_rate"])

            # 보유기간 약점 → exit_timing에 반영
            elif area == "holding_period":
                if data.get("short_term_wr", 50) > data.get("swing_wr", 50) + 15:
                    exit_cfg = self.state.active_config.setdefault("exit_timing", {})
                    exit_cfg["prefer_short_term"] = True
                    applied += 1
                    logger.info("복기 자동적용: 단기 보유 전략 강화")
                elif data.get("swing_wr", 50) > data.get("short_term_wr", 50) + 15:
                    exit_cfg = self.state.active_config.setdefault("exit_timing", {})
                    exit_cfg["prefer_short_term"] = False
                    applied += 1
                    logger.info("복기 자동적용: 장기 보유 전략 강화")

        return applied

    def _save_review(self, review: dict):
        """복기 결과를 저장한다."""
        self.REVIEW_FILE.parent.mkdir(parents=True, exist_ok=True)

        history = []
        if self.REVIEW_FILE.exists():
            try:
                history = json.loads(self.REVIEW_FILE.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, TypeError):
                pass

        history.append(review)
        # 최근 20건만 유지
        if len(history) > 20:
            history = history[-20:]

        self.REVIEW_FILE.write_text(
            json.dumps(history, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
