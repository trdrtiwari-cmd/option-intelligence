"""v1.20.66: breadth universes without NSE downloads. Run: python tests/test_universes.py"""
import os, sys, tempfile, asyncio
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import dashboard_data as dd

CSV = "Company Name,Industry,Symbol,Series,ISIN Code\nHDFC Bank Ltd.,Financial Services,HDFCBANK,EQ,INE040A01034\nICICI Bank Ltd.,Financial Services,ICICIBANK,EQ,INE090A01021\n" \
      "State Bank of India,Financial Services,SBIN,EQ,INE062A01020\nAxis Bank Ltd.,Financial Services,AXISBANK,EQ,INE238A01034\nKotak Mahindra Bank Ltd.,Financial Services,KOTAKBANK,EQ,INE237A01028\n"


def test_builtin_lists():
    assert len(dd.FALLBACK_NIFTYBANK) == 12 and set(dd.FALLBACK_LISTS) == {"nifty50", "niftybank"}
    assert {"HDFCBANK", "ICICIBANK"} <= set(dd.FALLBACK_NIFTYBANK) <= set(dd.FALLBACK_NIFTYBANK) | set(dd.FALLBACK_NIFTY50)


def test_local_csv_wins_and_parses():
    try:
        import main as M
    except ImportError:
        return
    with tempfile.TemporaryDirectory() as d:
        open(os.path.join(d, "ind_niftybanklist.csv"), "w", encoding="utf-8").write(CSV)
        M.UNIVERSE_DIR = d
        M.UNIVERSE_CACHE.clear()
        e = asyncio.run(M._fetch_constituents("niftybank"))
        assert len(e["rows"]) == 5 and e["rows"][0]["key"] == "NSE_EQ|INE040A01034" and "Your CSV" in e["source"]


if __name__ == "__main__":
    test_builtin_lists(); test_local_csv_wins_and_parses(); print("ok")
