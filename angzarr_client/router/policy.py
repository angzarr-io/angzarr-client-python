"""A policy's dispatch: ``PolicyService.Handle`` and ``OnRefused``.

A policy is stateless. Every page of the source book is a trigger: a
declared event type runs its handler, once per page, and the commands it
returns are answered as returned, grouped by triggering page
(``PolicyResponse.reactions``, in page order; none for an undeclared page).
The coordinator checks and stamps them (decision 14.42). A refusal of one of
the policy's commands runs the translating callback for the refused
command's type and answers its payload as a bare fact page, or answers no
translation; the coordinator builds and addresses the refusal fact.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from google.protobuf import any_pb2

from ..proto.io.angzarr.v1 import policy_pb2, types_pb2
from .context import CommandContext, PageContext, as_any, book_context, call, type_name

# Business code's shapes:
#   event:    (event Any, PageContext of its trigger) -> list of CommandBook
#   refused:  (Refusal, the refused command's CommandContext) -> message | Any
PolicyEventThunk = Callable[[any_pb2.Any, PageContext], list]
RefusalThunk = Callable[[policy_pb2.Refusal, CommandContext], object]


@dataclass
class PolicyDispatch:
    """One policy's dispatch table: its name, its one source domain, its
    one destination domain, its event handlers and its translating
    callbacks, each by fully-qualified type."""

    name: str
    input_domain: str
    output_domain: str
    events: dict[str, PolicyEventThunk] = field(default_factory=dict)
    refusals: dict[str, RefusalThunk] = field(default_factory=dict)
    rules_version: str = ""

    kind = types_pb2.COMPONENT_KIND_POLICY

    def on_event(self, full_name: str, thunk: Callable) -> PolicyDispatch:
        """Register the handler ``(event, source_cover)`` of one event type."""
        self.events[full_name] = lambda event, ctx: thunk(event, ctx.cover)
        return self

    def on_event_with_context(
        self, full_name: str, thunk: PolicyEventThunk
    ) -> PolicyDispatch:
        """Register the handler ``(event, PageContext)`` of one event type:
        it reads the source cover and its trigger's sequence."""
        self.events[full_name] = thunk
        return self

    def on_refused(self, command: str, thunk: RefusalThunk) -> PolicyDispatch:
        """Register the translating callback for refusals of ``command``:
        it returns a fact of the policy's source domain."""
        self.refusals[command] = thunk
        return self

    def with_rules_version(self, version: str) -> PolicyDispatch:
        """Set the build version the policy describes itself with."""
        self.rules_version = version
        return self

    def handle(self, source: types_pb2.EventBook) -> policy_pb2.PolicyResponse:
        """``PolicyService.Handle``: one reaction per handled page."""
        resp = policy_pb2.PolicyResponse()
        cover = source.cover if source.HasField("cover") else None
        for page in source.pages:
            thunk = self.events.get(type_name(page.payload.type_url))
            if thunk is None:
                continue
            ctx = PageContext(
                cover, page.sequence, page.meta if page.HasField("meta") else None
            )
            commands = call(ctx, thunk, page.payload, ctx) or []
            resp.reactions.add(trigger_sequence=page.sequence, commands=commands)
        return resp

    def refused(self, refusal: policy_pb2.Refusal) -> policy_pb2.OnRefusedResponse:
        """``PolicyService.OnRefused``: the translated fact's payload, alone,
        or no fact when nothing translates the refused command's type."""
        command = refusal.command
        thunk = self.refusals.get(type_name(command.page.command.payload.type_url))
        if thunk is None:
            return policy_pb2.OnRefusedResponse()
        ctx = book_context(command)
        payload = as_any(call(PageContext(ctx.cover), thunk, refusal, ctx))
        fact = types_pb2.CommandBook(
            page=types_pb2.CommandPage(fact=types_pb2.Fact(payload=payload))
        )
        return policy_pb2.OnRefusedResponse(fact=fact)
