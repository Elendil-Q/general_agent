"""Tests for follow_up tool via SubagentExecutor.continue_with_prompt."""

from unittest.mock import MagicMock, patch

pytest_plugins = ["test_subagent_executor"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def async_iterator(items):
    """Helper to create an async iterator from a list."""
    for item in items:
        yield item


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestContinueWithPrompt:
    """Test SubagentExecutor.continue_with_prompt() — the core of follow_up."""

    def test_continue_with_prompt_revives_idle_subagent(self, classes, base_config, msg):
        """Calling continue_with_prompt on an IDLE executor resumes and completes."""
        SubagentExecutor = classes["SubagentExecutor"]
        SubagentStatus = classes["SubagentStatus"]
        SubagentConfig = classes["SubagentConfig"]

        config = SubagentConfig(
            name="test-agent",
            description="Test agent",
            system_prompt="You are a test agent.",
            max_turns=10,
            timeout_seconds=60,
            keep_alive=True,
        )

        # First run: the agent returns a single AI message
        first_msg = msg.ai("First response", "msg-first")
        first_state = {"messages": [msg.human("Task 1"), first_msg]}

        mock_agent = MagicMock()
        mock_agent.astream = lambda *args, **kwargs: async_iterator([first_state])

        executor = SubagentExecutor(
            config=config,
            tools=[],
            thread_id="test-thread",
            task_id="task-123",
        )

        with patch.object(executor, "_create_agent", return_value=mock_agent):
            result = executor.execute("Task 1")

        assert result.status == SubagentStatus.IDLE, f"Expected IDLE, got {result.status}"

        # Second run (follow_up): new prompt, new AI response
        second_msg = msg.ai("Follow-up response", "msg-second")
        second_state = {"messages": [msg.human("Follow-up prompt"), second_msg]}

        mock_agent2 = MagicMock()
        mock_agent2.astream = lambda *args, **kwargs: async_iterator([second_state])

        # Replace the cached agent
        executor._agent = mock_agent2

        follow_up_result = executor.continue_with_prompt("Follow-up prompt", "task-123")

        # With keep_alive=True, stays IDLE after follow_up
        assert follow_up_result.status in (SubagentStatus.IDLE, SubagentStatus.COMPLETED), f"Expected IDLE or COMPLETED, got {follow_up_result.status}"
        assert follow_up_result.result == "Follow-up response"

    def test_continue_with_prompt_on_terminal_completes_anyway(self, classes, base_config, msg):
        """continue_with_prompt on a COMPLETED executor starts a fresh continuation.

        The registry guard is in the tool layer; the executor method runs regardless.
        """
        SubagentExecutor = classes["SubagentExecutor"]
        SubagentStatus = classes["SubagentStatus"]
        SubagentConfig = classes["SubagentConfig"]

        config = SubagentConfig(
            name="test-agent",
            description="Test agent",
            system_prompt="You are a test agent.",
            max_turns=10,
            timeout_seconds=60,
            keep_alive=False,
        )

        final_msg = msg.ai("Done", "msg-done")
        final_state = {"messages": [msg.human("Task"), final_msg]}

        mock_agent = MagicMock()
        mock_agent.astream = lambda *args, **kwargs: async_iterator([final_state])

        executor = SubagentExecutor(
            config=config,
            tools=[],
            thread_id="test-thread",
            task_id="task-456",
        )

        with patch.object(executor, "_create_agent", return_value=mock_agent):
            result = executor.execute("Task")

        assert result.status == SubagentStatus.COMPLETED, f"Expected COMPLETED, got {result.status}"

        # Even though the original is COMPLETED, continue_with_prompt still runs.
        # The checkpointer may or may not find prior state, but it shouldn't crash.
        follow_msg = msg.ai("Follow-up done", "msg-follow")
        follow_state = {"messages": [msg.human("Follow-up"), follow_msg]}

        mock_agent2 = MagicMock()
        mock_agent2.astream = lambda *args, **kwargs: async_iterator([follow_state])
        executor._agent = mock_agent2

        follow_up_result = executor.continue_with_prompt("Follow-up", "task-456")

        assert follow_up_result.status == SubagentStatus.COMPLETED
        assert follow_up_result.result == "Follow-up done"

    def test_continue_with_prompt_emits_revived_event(self, classes, base_config, msg):
        """continue_with_prompt emits a 'revived' lifecycle event on the event bus."""
        SubagentExecutor = classes["SubagentExecutor"]
        SubagentStatus = classes["SubagentStatus"]
        SubagentConfig = classes["SubagentConfig"]

        from deerflow.subagents.event_bus import event_bus

        config = SubagentConfig(
            name="test-agent",
            description="Test agent",
            system_prompt="You are a test agent.",
            max_turns=10,
            timeout_seconds=60,
            keep_alive=True,
        )

        first_msg = msg.ai("First", "msg-first")
        first_state = {"messages": [msg.human("Task"), first_msg]}

        mock_agent = MagicMock()
        mock_agent.astream = lambda *args, **kwargs: async_iterator([first_state])

        executor = SubagentExecutor(
            config=config,
            tools=[],
            thread_id="test-thread",
            task_id="task-789",
        )

        with patch.object(executor, "_create_agent", return_value=mock_agent):
            result = executor.execute("Task")

        assert result.status == SubagentStatus.IDLE

        # Subscribe to lifecycle events
        events: list[dict] = []
        unsub = event_bus.on("subagent:lifecycle", lambda p: events.append(p))

        try:
            second_msg = msg.ai("Second", "msg-second")
            second_state = {"messages": [msg.human("Follow-up"), second_msg]}

            mock_agent2 = MagicMock()
            mock_agent2.astream = lambda *args, **kwargs: async_iterator([second_state])
            executor._agent = mock_agent2

            executor.continue_with_prompt("Follow-up", "task-789")

            revived_events = [e for e in events if e.get("event") == "revived"]
            assert len(revived_events) >= 1, f"No 'revived' event emitted; got: {events}"
        finally:
            unsub()
