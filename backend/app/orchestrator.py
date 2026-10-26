import time
from dataclasses import replace
from typing import Any
from app.container import Container
from app.conversation.merger import merge
from app.conversation.state import PendingClarification
from app.decision.engine import DecisionContext, decide
from app.domain.constraints import ConstraintOp, ConstraintSet
from app.domain.enums import Decision, Intent, OpKind, RefusalReason, WarningCode
from app.domain.plans import SearchSpec
from app.generation.template_generator import generate_response
from app.metrics.recorder import Recorder
from app.nlp.constraint_extractor import parse_query
from app.schemas.chat import ChatRequest, ConstraintOpIn
from app.schemas.claims import ChatResponse, RefusalBlock


class Orchestrator:
    def __init__(self, container: Container):
        self.container = container

    def reset(self, session_id: str) -> None:
        self.container.sessions.reset(session_id)

    def handle(self, req: ChatRequest, request_id: str) -> ChatResponse:
        rec = Recorder(request_id)

        # 1. Session state
        state = self.container.sessions.get_or_create(req.session_id)
        turn_id = state.turn_no + 1

        # 2. Process incoming UI client context focus
        if req.client_context and req.client_context.focus_product_id:
            focus_p = self.container.product_repo.get(req.client_context.focus_product_id)
            if focus_p:
                state.focus_product_id = focus_p.product_id

        # 3. Handle resolution of pending clarification
        parsed = None
        if req.resolution and state.pending:
            pending = state.pending
            # Match option
            chosen_opt = next((o for o in pending.options if o.option_id == req.resolution.option_id), None)
            if chosen_opt:
                if chosen_opt.product_id:
                    # Replay with resolved product_id
                    parsed = parse_query(
                        chosen_opt.product_id,
                        view_has_focus=True,
                        vocab=self.container.vocab,
                        settings=self.container.settings,
                        product_repo=self.container.product_repo,
                    )
                elif chosen_opt.constraint_ops:
                    ops = [
                        ConstraintOp(
                            op=OpKind(co.op),
                            key=co.key,
                            value=co.value,
                            origin="CLARIFICATION",
                        )
                        for co in chosen_opt.constraint_ops
                    ]
                    parsed = parse_query(
                        "",
                        view_has_focus=bool(state.focus_product_id),
                        vocab=self.container.vocab,
                        settings=self.container.settings,
                        product_repo=self.container.product_repo,
                    )
                    parsed = parsed.__class__(
                        original="",
                        normalized=parsed.normalized,
                        intent=Intent.SEARCH,
                        requested_fields=(),
                        product_ids=(),
                        entity_terms=(),
                        references_focus=False,
                        policy_topic=None,
                        constraint_ops=tuple(ops),
                        this_turn_constraints=ConstraintSet(),
                        confidence=1.0,
                    )
                elif chosen_opt.policy_topic:
                    # D-06: the user picked a policy topic; replay the stored
                    # policy query resolved to that topic (no re-parsing).
                    base = pending.parsed
                    parsed = base.__class__(
                        original=base.original,
                        normalized=base.normalized,
                        intent=Intent.POLICY,
                        requested_fields=(),
                        product_ids=(),
                        entity_terms=(),
                        references_focus=False,
                        policy_topic=chosen_opt.policy_topic,
                        constraint_ops=base.constraint_ops,
                        this_turn_constraints=base.this_turn_constraints,
                        confidence=1.0,
                    )
            state.pending = None

        # 4. Parse query if not already resolved
        rec.start_stage("parse")
        if not parsed:
            has_focus = bool(state.focus_product_id)
            parsed = parse_query(
                req.message,
                view_has_focus=has_focus,
                vocab=self.container.vocab,
                settings=self.container.settings,
                product_repo=self.container.product_repo,
            )

        # Use only the immediately established conversational context to resolve
        # an otherwise ambiguous "available" follow-up.
        if parsed.available_ambiguous and state.availability_context == "stock":
            parsed = replace(parsed, available_ambiguous=False)
        elif parsed.available_ambiguous and state.availability_context == "catalogue":
            parsed = replace(
                parsed,
                intent=Intent.EXISTENCE,
                entity_terms=state.availability_terms,
                references_focus=False,
                available_ambiguous=False,
            )
        elif parsed.available_ambiguous and state.availability_context == "attribute":
            parsed = replace(parsed, available_ambiguous=False)

        # 5. Merge constraint ops (from text + client UI request)
        rec.start_stage("merge")
        all_ops = list(parsed.constraint_ops)
        for u_op in req.constraint_ops:
            all_ops.append(ConstraintOp(
                op=OpKind(u_op.op),
                key=u_op.key,
                value=u_op.value,
                origin="UI_ACTION",
            ))

        merge_res = merge(
            state.constraints,
            all_ops,
            additive_cue=parsed.additive_category_cue,
            vocab=self.container.vocab,
        )

        # 6. Retrieval stage
        rec.start_stage("retrieve")
        search_res = None
        entity_res = None
        id_lookup_res = None
        policy_res = None

        if parsed.intent == Intent.POLICY:
            terms = list(parsed.entity_terms) or req.message.split()
            policy_res = self.container.policy_retriever.search(
                terms,
                parsed.policy_topic,
                turn_id=turn_id,
            )

        elif parsed.product_ids:
            id_lookup_res = self.container.retriever.lookup_ids(parsed.product_ids, turn_id=turn_id)

        else:
            # Check for ambiguity on entity terms if asking about a specific item
            should_resolve_entity = parsed.intent in (
                Intent.PRODUCT_FACT,
                Intent.PRODUCT_WARRANTY,
                Intent.EXISTENCE,
            ) and bool(parsed.entity_terms)
            if parsed.intent == Intent.PRODUCT_WARRANTY and state.focus_product_id:
                # A focused product is already the trusted warranty subject; the
                # generic "warranty" token must not turn it into an entity tie.
                should_resolve_entity = False
            if should_resolve_entity:
                entity_res = self.container.retriever.resolve_entity(
                    parsed.intent,
                    parsed.entity_terms,
                    merge_res.constraints,
                    turn_id=turn_id,
                )

            # Standard search over catalogue
            spec = SearchSpec(
                intent=parsed.intent,
                constraints=merge_res.constraints,
                entity_terms=parsed.entity_terms,
                top_k=req.options.max_results,
            )
            search_res = self.container.retriever.search_catalogue(spec, turn_id=turn_id)

        # 7. Decision stage
        rec.start_stage("decide")
        ctx = DecisionContext(
            parsed=parsed,
            state_before=state,
            merge_res=merge_res,
            search=search_res,
            entity=entity_res,
            id_lookup=id_lookup_res,
            policy=policy_res,
            turn_id=turn_id,
            settings=self.container.settings,
            product_repo=self.container.product_repo,
        )
        plan = decide(ctx)

        # 8. Generation stage
        rec.start_stage("generate")
        show_search = self.container.settings.app.citation.get("search_marker", "SHOW") == "SHOW"
        draft, digest = generate_response(
            plan,
            self.container.product_repo,
            self.container.policy_repo,
            show_search_marker=show_search,
        )

        # 9. Validation stage
        rec.start_stage("validate")
        try:
            val_report = self.container.validator.validate(draft, digest)
        except Exception as exc:
            print(f"Validator exception in turn {turn_id}: {exc}")
            from app.domain.plans import ValidationReport, Violation
            val_report = ValidationReport(
                ok=False,
                violations=(Violation(rule="V-12", claim_id=None, detail=str(exc)),),
            )

        if not val_report.ok:
            rec.record_trip()
            print(f"Validation failure in turn {turn_id}: {val_report.violations}")
            # Fall back safely to refusal D-12
            refusal_block = RefusalBlock(reason=RefusalReason.INTERNAL_VALIDATION_FAILURE)
            draft = draft.__class__(
                decision=Decision.REFUSE,
                answer_text="I couldn't produce a verified answer to that. Please try rephrasing.",
                claims=[],
                evidence=[],
                products=[],
                clarification=None,
                refusal=refusal_block,
                suggestions=[],
            )

        # 10. Update state
        state.constraints = plan.state_update.constraints_to_persist
        state.focus_product_id = plan.state_update.focus_product_id
        state.last_result_ids = plan.state_update.last_result_ids
        if parsed.available_ambiguous:
            # Keep prior semantic context when asking the user to disambiguate.
            pass
        elif any(op.key == "stock" and op.op == OpKind.SET for op in parsed.constraint_ops):
            state.availability_context = "stock"
            state.availability_terms = ()
        elif parsed.intent == Intent.EXISTENCE:
            state.availability_context = "catalogue"
            state.availability_terms = parsed.entity_terms
        elif parsed.attribute_question and parsed.this_turn_constraints.attributes:
            state.availability_context = "attribute"
            state.availability_terms = ()
        else:
            state.availability_context = None
            state.availability_terms = ()
        if plan.clarification:
            state.pending = PendingClarification(
                clarification_id=draft.clarification.clarification_id,
                reason=plan.clarification.reason,
                options=plan.clarification.options,
                parsed=parsed,
            )
        else:
            state.pending = None
        state.turn_no = turn_id
        self.container.sessions.save(state)

        # 11. Final response assembly
        active_constraints = self.container.retriever._constraints_to_views(state.constraints)
        metrics = rec.finish()

        return ChatResponse(
            session_id=state.session_id,
            turn_id=turn_id,
            decision=draft.decision,
            answer_text=draft.answer_text,
            claims=draft.claims,
            evidence=draft.evidence,
            products=draft.products,
            clarification=draft.clarification,
            refusal=draft.refusal,
            active_constraints=active_constraints,
            suggestions=draft.suggestions,
            warnings=list(plan.warnings),
            metrics=metrics,
        )
