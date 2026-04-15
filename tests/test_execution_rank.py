from execution import should_use_llm_rank, summarize_reasons


def test_summarize_reasons_limits_output():
    text = summarize_reasons(["a", "b", "c", "d", "e"], limit=3)
    assert text == "a,b,c"


def test_should_use_llm_rank_requires_close_scores():
    cfg = {
        "use_llm_secondary_rank": True,
        "llm_rank_top_n": 3,
        "llm_rank_min_score": 72,
        "llm_rank_max_score_spread": 6,
    }
    candidates = [{"score": 81}, {"score": 79}, {"score": 76}]
    ok, reason = should_use_llm_rank(candidates, cfg)
    assert ok
    assert reason == "use_llm_rank"


def test_should_use_llm_rank_skips_clear_winner():
    cfg = {
        "use_llm_secondary_rank": True,
        "llm_rank_top_n": 3,
        "llm_rank_min_score": 72,
        "llm_rank_max_score_spread": 6,
    }
    candidates = [{"score": 88}, {"score": 77}, {"score": 74}]
    ok, reason = should_use_llm_rank(candidates, cfg)
    assert not ok
    assert reason == "top_score_clear_winner"


def test_should_use_llm_rank_skips_low_scores():
    cfg = {
        "use_llm_secondary_rank": True,
        "llm_rank_top_n": 3,
        "llm_rank_min_score": 72,
        "llm_rank_max_score_spread": 6,
    }
    candidates = [{"score": 69}, {"score": 68}, {"score": 67}]
    ok, reason = should_use_llm_rank(candidates, cfg)
    assert not ok
    assert reason == "top_score_below_min"
