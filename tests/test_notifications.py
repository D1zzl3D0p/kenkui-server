from uuid import UUID, uuid4

import pytest

from kenkui_server.jobs.models import (
    Job,
    JobSpec,
    JobStatus,
    OutputSpec,
    SingleVoiceCasting,
    TtsSettings,
)
from kenkui_server.notifications.composition import (
    api_origin_from,
    notification_settings_from_environment,
)
from kenkui_server.notifications.mailer import (
    ICLOUD_SMTP_HOST,
    ICLOUD_SMTP_PORT,
    RecordingMailer,
    smtp_config_from_environment,
)
from kenkui_server.notifications.messages import (
    completion_email,
    unsubscribe_token,
    unsubscribe_url,
    verify_unsubscribe_token,
)
from kenkui_server.notifications.service import (
    EMAIL_CHANNEL,
    CompletionNotifier,
    Recipient,
)

SECRET = "a-signing-secret"
IDENTITY = UUID("11111111-2222-3333-4444-555555555555")


def succeeded_job(title: str | None = "Middlemarch") -> Job:
    return Job(
        id="job-1",
        spec=JobSpec(
            source_id="asset-1",
            chapters=("chapter-a",),
            casting=SingleVoiceCasting(voice_id="en_US-amy"),
            tts=TtsSettings(),
            output=OutputSpec(path="/tmp/book.m4b", title=title),
        ),
        status=JobStatus.SUCCEEDED,
    )


class FakeRepository:
    """In-memory stand-in honouring the claim-before-send contract."""

    def __init__(self, recipient: Recipient | None) -> None:
        self._recipient = recipient
        self.claims: list[tuple[str, str]] = []
        self.delivered: list[tuple[str, str]] = []

    def recipient_for_job(self, job_id: str) -> Recipient | None:
        return self._recipient

    def claim(self, job_id: str, channel: str) -> bool:
        if (job_id, channel) in self.claims:
            return False
        self.claims.append((job_id, channel))
        return True

    def mark_delivered(self, job_id: str, channel: str) -> None:
        self.delivered.append((job_id, channel))


def notifier(repository: FakeRepository, mailer: RecordingMailer) -> CompletionNotifier:
    return CompletionNotifier(
        repository,
        mailer,
        web_origin="https://app.kenkui.fm",
        api_origin="https://api.kenkui.fm",
        unsubscribe_secret=SECRET,
    )


def test_unsubscribe_token_round_trips_and_rejects_another_identity() -> None:
    token = unsubscribe_token(IDENTITY, SECRET)
    assert verify_unsubscribe_token(IDENTITY, SECRET, token)
    assert not verify_unsubscribe_token(uuid4(), SECRET, token)
    assert not verify_unsubscribe_token(IDENTITY, "another-secret", token)


def test_unsubscribe_url_targets_the_api_and_carries_its_signature() -> None:
    url = unsubscribe_url("https://api.kenkui.fm/", IDENTITY, SECRET)
    assert url.startswith("https://api.kenkui.fm/v1/notifications/unsubscribe?")
    assert f"identity={IDENTITY}" in url
    assert unsubscribe_token(IDENTITY, SECRET) in url


def test_completion_email_names_the_book_and_offers_one_click_unsubscribe() -> None:
    message = completion_email(
        to="reader@example.com",
        job_id="job-1",
        title="Middlemarch",
        web_origin="https://app.kenkui.fm",
        unsubscribe="https://api.kenkui.fm/unsub",
    )
    assert "Middlemarch" in message.subject
    assert "https://app.kenkui.fm/jobs/job-1" in message.text
    assert message.headers["List-Unsubscribe"] == "<https://api.kenkui.fm/unsub>"
    assert message.html is not None and "Middlemarch" in message.html


def test_completion_email_falls_back_when_the_job_named_no_title() -> None:
    message = completion_email(
        to="reader@example.com",
        job_id="job-1",
        title=None,
        web_origin="https://app.kenkui.fm",
        unsubscribe="https://api.kenkui.fm/unsub",
    )
    assert message.subject == "Your audiobook is ready"


def test_completion_mails_the_owner_once_and_records_delivery() -> None:
    repository = FakeRepository(Recipient(IDENTITY, "reader@example.com", True))
    mailer = RecordingMailer()
    notifier(repository, mailer).completed(succeeded_job())
    assert [message.to for message in mailer.sent] == ["reader@example.com"]
    assert repository.delivered == [("job-1", EMAIL_CHANNEL)]


def test_a_replayed_completion_cannot_mail_twice() -> None:
    repository = FakeRepository(Recipient(IDENTITY, "reader@example.com", True))
    mailer = RecordingMailer()
    service = notifier(repository, mailer)
    service.completed(succeeded_job())
    service.completed(succeeded_job())
    assert len(mailer.sent) == 1


@pytest.mark.parametrize(
    "recipient",
    [
        None,
        Recipient(IDENTITY, None, True),
        Recipient(IDENTITY, "reader@example.com", False),
    ],
)
def test_no_address_or_no_consent_sends_nothing(recipient: Recipient | None) -> None:
    repository = FakeRepository(recipient)
    mailer = RecordingMailer()
    notifier(repository, mailer).completed(succeeded_job())
    assert mailer.sent == []
    assert repository.claims == []


def test_a_failing_mailer_never_breaks_a_finished_job() -> None:
    class ExplodingMailer:
        def send(self, message: object) -> None:
            raise RuntimeError("smtp is down")

    repository = FakeRepository(Recipient(IDENTITY, "reader@example.com", True))
    notifier(repository, ExplodingMailer()).completed(succeeded_job())  # type: ignore[arg-type]
    assert repository.delivered == []


def test_smtp_configuration_defaults_to_icloud_and_separates_login_from_sender() -> None:
    config = smtp_config_from_environment(
        {
            "KENKUI_SMTP_PASSWORD": "app-specific",
            "KENKUI_SMTP_SENDER": "team@kenkui.fm",
            "KENKUI_SMTP_USERNAME": "owner@icloud.com",
        }
    )
    assert config is not None
    assert (config.host, config.port) == (ICLOUD_SMTP_HOST, ICLOUD_SMTP_PORT)
    assert config.username == "owner@icloud.com"
    assert config.sender == "team@kenkui.fm"


def test_smtp_username_defaults_to_the_sender_address() -> None:
    config = smtp_config_from_environment(
        {"KENKUI_SMTP_PASSWORD": "app-specific", "KENKUI_SMTP_SENDER": "team@kenkui.fm"}
    )
    assert config is not None and config.username == "team@kenkui.fm"


@pytest.mark.parametrize(
    "environment",
    [{}, {"KENKUI_SMTP_PASSWORD": "app-specific"}, {"KENKUI_SMTP_SENDER": "team@kenkui.fm"}],
)
def test_partial_smtp_configuration_stays_unconfigured(environment: dict[str, str]) -> None:
    assert smtp_config_from_environment(environment) is None


def test_api_origin_falls_back_to_the_auth_callback_host() -> None:
    assert (
        api_origin_from({"WORKOS_REDIRECT_URI": "https://api.kenkui.fm/v1/auth/callback"})
        == "https://api.kenkui.fm"
    )
    assert api_origin_from({"KENKUI_API_ORIGIN": "https://other.example/"}) == "https://other.example"
    assert api_origin_from({}) == ""


def test_notification_settings_require_mail_links_and_a_signing_key() -> None:
    complete = {
        "KENKUI_SMTP_PASSWORD": "app-specific",
        "KENKUI_SMTP_SENDER": "team@kenkui.fm",
        "KENKUI_WEB_ORIGIN": "https://app.kenkui.fm/",
        "KENKUI_UNSUBSCRIBE_SECRET": SECRET,
        "KENKUI_API_ORIGIN": "https://api.kenkui.fm",
    }
    settings = notification_settings_from_environment(complete)
    assert settings is not None and settings.web_origin == "https://app.kenkui.fm"
    for missing in ("KENKUI_SMTP_PASSWORD", "KENKUI_WEB_ORIGIN", "KENKUI_UNSUBSCRIBE_SECRET"):
        assert notification_settings_from_environment(
            {key: value for key, value in complete.items() if key != missing}
        ) is None
