import sys


def test_top_level_skills_flag_defaults_to_chat(monkeypatch):
    import kclaw_cli.main as main_mod

    captured = {}

    def fake_cmd_chat(args):
        captured["skills"] = args.skills
        captured["command"] = args.command

    monkeypatch.setattr(main_mod, "cmd_chat", fake_cmd_chat)
    monkeypatch.setattr(
        sys,
        "argv",
        ["kclaw", "-s", "kclaw-dev,github-auth"],
    )

    main_mod.main()

    assert captured == {
        "skills": ["kclaw-dev,github-auth"],
        "command": None,
    }


def test_chat_subcommand_accepts_skills_flag(monkeypatch):
    import kclaw_cli.main as main_mod

    captured = {}

    def fake_cmd_chat(args):
        captured["skills"] = args.skills
        captured["query"] = args.query

    monkeypatch.setattr(main_mod, "cmd_chat", fake_cmd_chat)
    monkeypatch.setattr(
        sys,
        "argv",
        ["kclaw", "chat", "-s", "github-auth", "-q", "hello"],
    )

    main_mod.main()

    assert captured == {
        "skills": ["github-auth"],
        "query": "hello",
    }


def test_continue_worktree_and_skills_flags_work_together(monkeypatch):
    import kclaw_cli.main as main_mod

    captured = {}

    def fake_cmd_chat(args):
        captured["continue_last"] = args.continue_last
        captured["worktree"] = args.worktree
        captured["skills"] = args.skills
        captured["command"] = args.command

    monkeypatch.setattr(main_mod, "cmd_chat", fake_cmd_chat)
    monkeypatch.setattr(
        sys,
        "argv",
        ["kclaw", "-c", "-w", "-s", "kclaw-dev"],
    )

    main_mod.main()

    assert captured == {
        "continue_last": True,
        "worktree": True,
        "skills": ["kclaw-dev"],
        "command": "chat",
    }
