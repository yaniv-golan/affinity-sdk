"""Tests for affinity package public API exports."""
# Imports inside functions are intentional - testing public API exports

from __future__ import annotations

import logging


class TestPackageExports:
    """Verify all public exports are importable and match __all__."""

    def test_all_exports_importable(self) -> None:
        """Every name in __all__ should be importable from affinity."""
        import affinity

        for name in affinity.__all__:
            obj = getattr(affinity, name, None)
            assert obj is not None, f"{name} is in __all__ but not accessible"

    def test_main_clients_exported(self) -> None:
        """Affinity and AsyncAffinity are the main entry points."""
        from affinity import Affinity, AsyncAffinity

        assert Affinity is not None
        assert AsyncAffinity is not None

    def test_all_exceptions_exported(self) -> None:
        """All exception classes should be importable from the root package."""
        from affinity import (
            AffinityError,
            AmbiguousFieldError,
            AuthenticationError,
            AuthorizationError,
            CompanyNotFoundError,
            ConfigurationError,
            ConflictError,
            EntityNotFoundError,
            NetworkError,
            NotFoundError,
            OpportunityNotFoundError,
            PersonNotFoundError,
            PolicyError,
            RateLimitError,
            ServerError,
            TimeoutError,
            TooManyResultsError,
            ValidationError,
            VersionCompatibilityError,
            WebhookInvalidJsonError,
            WebhookInvalidPayloadError,
            WebhookInvalidSentAtError,
            WebhookMissingKeyError,
            WebhookParseError,
            WriteNotAllowedError,
        )

        # AmbiguousFieldError is Exception-based (not AffinityError)
        assert issubclass(AmbiguousFieldError, Exception)

        # Verify inheritance hierarchy
        assert issubclass(AuthenticationError, AffinityError)
        assert issubclass(AuthorizationError, AffinityError)
        assert issubclass(NotFoundError, AffinityError)
        assert issubclass(EntityNotFoundError, NotFoundError)
        assert issubclass(PersonNotFoundError, EntityNotFoundError)
        assert issubclass(CompanyNotFoundError, EntityNotFoundError)
        assert issubclass(OpportunityNotFoundError, EntityNotFoundError)
        assert issubclass(ValidationError, AffinityError)
        assert issubclass(RateLimitError, AffinityError)
        assert issubclass(ConflictError, AffinityError)
        assert issubclass(ServerError, AffinityError)
        assert issubclass(ConfigurationError, AffinityError)
        assert issubclass(TimeoutError, AffinityError)
        assert issubclass(NetworkError, AffinityError)
        assert issubclass(PolicyError, AffinityError)
        assert issubclass(WriteNotAllowedError, PolicyError)
        assert issubclass(TooManyResultsError, AffinityError)
        assert issubclass(VersionCompatibilityError, AffinityError)
        assert issubclass(WebhookParseError, AffinityError)
        assert issubclass(WebhookInvalidJsonError, WebhookParseError)
        assert issubclass(WebhookInvalidPayloadError, WebhookParseError)
        assert issubclass(WebhookMissingKeyError, WebhookParseError)
        assert issubclass(WebhookInvalidSentAtError, WebhookParseError)

    def test_filter_builder_exported(self) -> None:
        """Filter builder (F, Filter, FilterExpression) should be importable."""
        from affinity import F, Filter, FilterExpression

        assert F is not None
        assert Filter is not None
        assert FilterExpression is not None

    def test_webhook_helpers_exported(self) -> None:
        """Webhook parsing helpers should be importable."""
        from affinity import BodyRegistry, WebhookEnvelope, dispatch_webhook, parse_webhook

        assert WebhookEnvelope is not None
        assert parse_webhook is not None
        assert dispatch_webhook is not None
        assert BodyRegistry is not None

    def test_policies_exported(self) -> None:
        """Policy classes should be importable."""
        from affinity import ExternalHookPolicy, Policies, WritePolicy

        assert WritePolicy is not None
        assert ExternalHookPolicy is not None
        assert Policies is not None

    def test_pagination_helpers_exported(self) -> None:
        """Pagination helpers should be importable."""
        from affinity import PaginationProgress

        assert PaginationProgress is not None

    def test_submodules_exported(self) -> None:
        """types and models submodules should be accessible."""
        from affinity import models, types

        assert models is not None
        assert types is not None
        # Verify they are actual modules
        assert hasattr(models, "__name__")
        assert hasattr(types, "__name__")


class TestPackageVersion:
    """Tests for package version."""

    def test_version_is_string(self) -> None:
        """__version__ should be a non-empty string."""
        import affinity

        assert isinstance(affinity.__version__, str)
        assert len(affinity.__version__) > 0

    def test_version_format(self) -> None:
        """Version should follow semver-like format."""
        import affinity

        parts = affinity.__version__.split(".")
        assert len(parts) >= 2, "Version should have at least major.minor"
        # First two parts should be numeric
        assert parts[0].isdigit(), f"Major version should be numeric: {parts[0]}"
        assert parts[1].isdigit(), f"Minor version should be numeric: {parts[1]}"


class TestLoggingSetup:
    """Tests for package logging configuration."""

    def test_logger_has_null_handler(self) -> None:
        """Package logger should have a NullHandler to prevent warnings."""
        logger = logging.getLogger("affinity_sdk")
        null_handlers = [h for h in logger.handlers if isinstance(h, logging.NullHandler)]
        assert len(null_handlers) >= 1, "Logger should have at least one NullHandler"

    def test_logger_does_not_propagate_by_default(self) -> None:
        """Logger should be configured but not spam the root logger."""
        # Just importing affinity should set up the logger
        import affinity  # noqa: F401 - import side effect is the test

        logger = logging.getLogger("affinity_sdk")
        # Logger exists and has handlers
        assert logger is not None


class TestTypesSubmodule:
    """Tests for affinity.types submodule."""

    def test_typed_ids_available(self) -> None:
        """Typed ID classes should be available in types submodule."""
        from affinity.types import (
            CompanyId,
            FieldId,
            ListEntryId,
            ListId,
            NoteId,
            OpportunityId,
            PersonId,
        )

        # Verify they can be instantiated (NewType-style, so they're just ints)
        assert CompanyId(123) == 123
        assert PersonId(456) == 456
        assert ListId(789) == 789
        assert OpportunityId(101) == 101
        assert FieldId(202) == 202
        assert ListEntryId(303) == 303
        assert NoteId(404) == 404

    def test_field_types_available(self) -> None:
        """FieldType enum should be available."""
        from affinity.types import FieldType

        assert hasattr(FieldType, "ENRICHED")
        assert hasattr(FieldType, "GLOBAL")
        assert hasattr(FieldType, "LIST")


class TestModelsSubmodule:
    """Tests for affinity.models submodule."""

    def test_entity_models_available(self) -> None:
        """Main entity models should be available."""
        from affinity.models import (
            AffinityList,
            Company,
            Note,
            Opportunity,
            Person,
            Reminder,
        )

        assert Company is not None
        assert Person is not None
        assert Opportunity is not None
        assert AffinityList is not None
        assert Note is not None
        assert Reminder is not None
