"""make_client() must take THETADATA_API_KEY from .env even when an old key is already set, and never print it."""
import theta

OLD_KEY = "old-shell-key-not-real"
NEW_KEY = "new-dotenv-key-not-real"


def test_dotenv_key_overrides_old_environment_key(tmp_path, monkeypatch, capsys):
    dotenv = tmp_path / ".env"
    dotenv.write_text(f'THETADATA_API_KEY="{NEW_KEY}"\n', encoding="utf-8")
    monkeypatch.setattr(theta.config, "DOTENV_PATH", dotenv)
    monkeypatch.setenv("THETADATA_API_KEY", OLD_KEY)

    seen = {}

    def fake_client(**kwargs):
        import os
        from dotenv import load_dotenv
        load_dotenv(dotenv_path=kwargs["dotenv_path"])  # same as the real library: no override
        seen["key"] = os.getenv("THETADATA_API_KEY")
        seen["kwargs"] = kwargs

    monkeypatch.setattr(theta, "ThetaClient", fake_client)
    theta.make_client()

    assert seen["key"] == NEW_KEY
    assert seen["kwargs"]["dotenv_path"] == dotenv
    out = capsys.readouterr()
    assert NEW_KEY not in out.out + out.err and OLD_KEY not in out.out + out.err


def test_environment_key_kept_when_dotenv_has_none(tmp_path, monkeypatch):
    dotenv = tmp_path / ".env"
    dotenv.write_text("OTHER=1\n", encoding="utf-8")
    monkeypatch.setattr(theta.config, "DOTENV_PATH", dotenv)
    monkeypatch.setenv("THETADATA_API_KEY", OLD_KEY)

    theta.use_dotenv_key()

    import os
    assert os.getenv("THETADATA_API_KEY") == OLD_KEY
