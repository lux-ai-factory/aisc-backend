"""
The risk mapper, pure: what a run links, what it sends back, what it drops.

The model itself is a test double (there is no model in CI); every rule the
pilot states is exercised against `suggest` directly, so the double never
stands in for our own logic.
"""
from django.test import TestCase

from aisc_backend.services import risk_mapper


def _offered():
    return [
        {"id": "O1", "text": "Operator oversight capability"},
        {"id": "O2", "text": "Governance of oversight roles"},
    ]


RISK = {"pid": "r1", "chain": "Wrongly rejected stale data loan refused"}


def _proposer(answers):
    """A model double: each call pops the next answer (a list of proposals)."""
    calls = []

    async def propose(risk, offered):
        calls.append((risk, offered))
        return answers.pop(0) if answers else []

    propose.calls = calls
    return propose


class SuggestTest(TestCase):

    async def test_a_quoting_proposal_is_linked(self):
        propose = _proposer([[{"objective_id": "O1", "quote": "loan refused"}]])
        result = await risk_mapper.suggest(
            risks=[RISK], offered=_offered(), existing={"r1": []}, propose=propose,
        )
        self.assertEqual(result["attempts"], 1)
        self.assertEqual(result["created"], [
            {"risk": "r1", "objective_id": "O1", "quote": "loan refused"}
        ])
        self.assertEqual(result["dropped"], [])

    async def test_quote_matching_ignores_case_and_whitespace(self):
        propose = _proposer([[{"objective_id": "O1", "quote": "STALE   DATA"}]])
        result = await risk_mapper.suggest(
            risks=[RISK], offered=_offered(), existing={"r1": []}, propose=propose,
        )
        self.assertEqual(len(result["created"]), 1)

    async def test_an_objective_outside_the_profile_is_sent_back(self):
        propose = _proposer([[{"objective_id": "O9", "quote": "loan refused"}]])
        result = await risk_mapper.suggest(
            risks=[RISK], offered=_offered(), existing={"r1": []}, propose=propose,
        )
        self.assertEqual(result["created"], [])
        self.assertEqual(result["dropped"][0]["reason"], "objective O9 is not in the profile")

    async def test_a_quote_that_is_not_in_the_chain_is_sent_back(self):
        propose = _proposer([[{"objective_id": "O1", "quote": "a made up quote"}]])
        result = await risk_mapper.suggest(
            risks=[RISK], offered=_offered(), existing={"r1": []}, propose=propose,
        )
        self.assertEqual(result["created"], [])
        self.assertEqual(result["dropped"][0]["reason"], "the quote is not in the risk chain")

    async def test_a_repeated_objective_is_sent_back(self):
        propose = _proposer([[
            {"objective_id": "O1", "quote": "loan refused"},
            {"objective_id": "O1", "quote": "stale data"},
        ]])
        result = await risk_mapper.suggest(
            risks=[RISK], offered=_offered(), existing={"r1": []}, propose=propose,
        )
        self.assertEqual([c["objective_id"] for c in result["created"]], ["O1"])
        self.assertEqual(result["dropped"][0]["reason"], "objective O1 is already linked to this risk")

    async def test_a_failing_model_is_retried_for_at_most_three_attempts(self):
        propose = _proposer([[], [], []])
        result = await risk_mapper.suggest(
            risks=[RISK], offered=_offered(), existing={"r1": []}, propose=propose,
        )
        self.assertEqual(result["attempts"], 3)
        self.assertEqual(len(propose.calls), 3)
        self.assertEqual(result["created"], [])
        self.assertEqual(
            result["dropped"], [{
                "risk": "r1", "objective_id": None,
                "reason": "no valid proposal within 3 attempt(s)",
            }]
        )

    async def test_a_bad_proposal_is_retried_and_can_succeed_on_the_next_attempt(self):
        propose = _proposer([
            [{"objective_id": "O1", "quote": "not in the chain"}],
            [{"objective_id": "O1", "quote": "loan refused"}],
        ])
        result = await risk_mapper.suggest(
            risks=[RISK], offered=_offered(), existing={"r1": []}, propose=propose,
        )
        self.assertEqual(result["attempts"], 2)
        self.assertEqual(len(result["created"]), 1)
        # the failed attempt is still reported as dropped
        self.assertEqual(result["dropped"][0]["reason"], "the quote is not in the risk chain")

    async def test_a_raising_model_does_not_abort_the_run(self):
        calls = []

        async def propose(risk, offered):
            calls.append(risk)
            if len(calls) == 1:
                raise RuntimeError("endpoint down")
            return [{"objective_id": "O2", "quote": "stale data"}]

        result = await risk_mapper.suggest(
            risks=[RISK], offered=_offered(), existing={"r1": []}, propose=propose,
        )
        self.assertEqual(result["attempts"], 2)
        self.assertEqual(result["created"][0]["objective_id"], "O2")

    async def test_a_hand_link_is_kept_and_the_risk_is_not_proposed_again(self):
        propose = _proposer([[{"objective_id": "O1", "quote": "loan refused"}]])
        result = await risk_mapper.suggest(
            risks=[RISK], offered=_offered(),
            existing={"r1": [{"objective_id": "O1", "source": "person"}]},
            propose=propose,
        )
        self.assertEqual(propose.calls, [])
        self.assertEqual(result["created"], [])

    async def test_also_replace_manual_proposes_over_hand_links(self):
        propose = _proposer([[{"objective_id": "O2", "quote": "loan refused"}]])
        result = await risk_mapper.suggest(
            risks=[RISK], offered=_offered(),
            existing={"r1": [{"objective_id": "O1", "source": "person"}]},
            propose=propose, also_replace_manual=True,
        )
        self.assertEqual([c["objective_id"] for c in result["created"]], ["O2"])

    async def test_a_risk_with_only_ai_links_is_proposed_again(self):
        propose = _proposer([[
            {"objective_id": "O1", "quote": "loan refused"},
            {"objective_id": "O2", "quote": "stale data"},
        ]])
        result = await risk_mapper.suggest(
            risks=[RISK], offered=_offered(),
            existing={"r1": [{"objective_id": "O1", "source": "ai"}]},
            propose=propose,
        )
        # an earlier AI link is the run's own work: it may be refreshed
        self.assertEqual(
            [c["objective_id"] for c in result["created"]], ["O1", "O2"]
        )

    async def test_a_risk_that_gets_a_link_is_not_proposed_again(self):
        second = {"pid": "r2", "chain": "Group divergence biased features"}
        propose = _proposer([
            [{"objective_id": "O1", "quote": "loan refused"}],
            [{"objective_id": "O2", "quote": "biased features"}],
        ])
        result = await risk_mapper.suggest(
            risks=[RISK, second], offered=_offered(),
            existing={"r1": [], "r2": []}, propose=propose,
        )
        self.assertEqual(result["attempts"], 1)
        self.assertEqual({c["risk"] for c in result["created"]}, {"r1", "r2"})


class ExtractJsonTest(TestCase):

    async def test_a_fenced_json_array_is_parsed(self):
        reply = '```json\n[{"objective_id": "O1", "quote": "x"}]\n```'
        self.assertEqual(
            risk_mapper.extract_json(reply), [{"objective_id": "O1", "quote": "x"}]
        )

    async def test_a_bare_object_with_proposals_is_parsed(self):
        reply = '{"proposals": [{"objective_id": "O2", "quote": "y"}]}'
        self.assertEqual(
            risk_mapper.extract_json(reply), [{"objective_id": "O2", "quote": "y"}]
        )

    async def test_prose_around_the_json_is_ignored(self):
        reply = 'Here you go: [{"objective_id": "O1", "quote": "z"}] — hope that helps'
        self.assertEqual(
            risk_mapper.extract_json(reply), [{"objective_id": "O1", "quote": "z"}]
        )

    async def test_a_reply_without_json_is_empty(self):
        self.assertEqual(risk_mapper.extract_json("I cannot help with that"), [])

    async def test_the_prompt_names_the_chain_and_the_objectives(self):
        prompt = risk_mapper.build_prompt(
            {"pid": "r1", "chain": "the chain"}, [{"id": "O1", "text": "oversight"}]
        )
        self.assertIn("the chain", prompt)
        self.assertIn("O1: oversight", prompt)
        self.assertIn("quote", prompt)
