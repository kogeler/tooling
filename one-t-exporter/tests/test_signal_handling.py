"""Signal handling and shutdown tests."""

import signal
import threading
import time
import unittest
from unittest.mock import MagicMock, call, patch

from tests.common import EXPORTER_MODULE, clear_validator_env, one_t_exporter


class TestSignalHandling(unittest.TestCase):
    """Test signal handling and graceful shutdown."""

    def setUp(self):
        """Reset shutdown event before each test."""
        clear_validator_env()

        # Reset shutdown event
        one_t_exporter.shutdown_event = threading.Event()
        one_t_exporter.health_server = None

    def test_signal_handler_sets_shutdown_event(self):
        """Test that signal handler sets the shutdown event."""
        from one_t_exporter import shutdown_event, signal_handler

        # Ensure shutdown event is not set initially
        self.assertFalse(shutdown_event.is_set())

        # Call signal handler (simulate SIGINT)
        signal_handler(signal.SIGINT, None)

        # Check that shutdown event is now set
        self.assertTrue(shutdown_event.is_set())

    def test_signal_handler_stops_health_server(self):
        """Test that signal handler stops the health server."""
        # Mock health server
        mock_server = MagicMock()
        one_t_exporter.health_server = mock_server

        # Call signal handler
        one_t_exporter.signal_handler(signal.SIGTERM, None)

        # Verify health server shutdown was called
        mock_server.shutdown.assert_called_once()

    @patch(f"{EXPORTER_MODULE}.signal.signal")
    def test_signal_handlers_registered(self, mock_signal):
        """Test that signal handlers are registered during main startup."""
        # Mock other dependencies to avoid actual server start
        with patch(f"{EXPORTER_MODULE}.start_http_server"):
            with patch(f"{EXPORTER_MODULE}.start_health_server"):
                with patch(f"{EXPORTER_MODULE}.update_metrics"):
                    with patch("sys.exit"):
                        # Set shutdown event to exit immediately
                        one_t_exporter.shutdown_event.set()

                        # Call main
                        one_t_exporter.main()

                        # Verify signal handlers were registered
                        calls = mock_signal.call_args_list
                        self.assertIn(
                            call(signal.SIGINT, one_t_exporter.signal_handler),
                            calls,
                        )
                        self.assertIn(
                            call(signal.SIGTERM, one_t_exporter.signal_handler),
                            calls,
                        )

    def test_main_loop_exits_on_shutdown_event(self):
        """Test that main loop exits when shutdown event is set."""
        from one_t_exporter import main, shutdown_event

        # Set up to exit immediately
        shutdown_event.set()

        # Mock dependencies
        with patch(f"{EXPORTER_MODULE}.start_http_server"):
            with patch(f"{EXPORTER_MODULE}.start_health_server"):
                with patch(f"{EXPORTER_MODULE}.update_metrics") as mock_update:
                    with patch("sys.exit") as mock_exit:
                        # Call main
                        main()

                        # Verify update_metrics was not called (loop should exit immediately)
                        mock_update.assert_not_called()

                        # Verify graceful exit
                        mock_exit.assert_called_once_with(0)

    def test_shutdown_event_interrupts_sleep(self):
        """Test that shutdown event interrupts the sleep period."""
        # Set short collection period for testing
        original_period = one_t_exporter.ONE_T_COLLECT_PERIOD
        one_t_exporter.ONE_T_COLLECT_PERIOD = 5

        try:
            # Track timing
            start_time = time.time()

            # Start main in a thread
            def run_main():
                with patch(
                    f"{EXPORTER_MODULE}.signal.signal"
                ):  # Mock signal registration
                    with patch(f"{EXPORTER_MODULE}.start_http_server"):
                        with patch(f"{EXPORTER_MODULE}.start_health_server"):
                            with patch(f"{EXPORTER_MODULE}.update_metrics"):
                                with patch("sys.exit"):
                                    one_t_exporter.main()

            main_thread = threading.Thread(target=run_main)
            main_thread.start()

            # Wait a moment then send shutdown signal
            time.sleep(1)
            one_t_exporter.shutdown_event.set()

            # Wait for thread to finish
            main_thread.join(timeout=3)

            # Verify it exited quickly (not waiting full collection period)
            elapsed = time.time() - start_time
            self.assertLess(elapsed, 3, "Should exit quickly after shutdown event")

        finally:
            # Restore original period
            one_t_exporter.ONE_T_COLLECT_PERIOD = original_period
