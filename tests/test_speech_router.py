import pytest
import yaml
from unittest.mock import patch
from haqdaar.engine import step, CallState
from haqdaar.bank import Bank

@pytest.fixture
def test_bank():
    yaml_content = """
    meta:
      version: 3
      question_count: 2
      language_default: hi
      max_questions_per_call: 10
      target_questions_per_call: 6
      stop_when_candidates_lte: 5
    controls: {}
    defaults: {}
    silence_ladder: []
    speech_policy:
      min_confidence: 0.5
      max_speech_attempts: 2
      on_unclear: {hi: "UNCLEAR"}
      after_max: {hi: "FALLBACK"}
    invalid_policy:
      max_invalid: 3
      on_invalid: {hi: "INVALID"}
      on_max_invalid: {hi: "MAX INVALID"}
      then: repeat_question
    questions:
      - id: Q001_LANGUAGE
        writes: language
        prompt_hi: "1 for hindi, 2 for english"
        dtmf:
          "1": {hi: "Hindi", set: {language: "hi"}}
          "2": {hi: "English", set: {language: "en"}}
      - id: Q002_SOMETHING
        requires: "language"
        writes: something
        prompt_hi: "Are you a farmer? 1 for yes, 2 for no"
        dtmf:
          "1": {hi: "Yes", set: {something: "yes"}}
          "2": {hi: "No", set: {something: "no"}}
    """
    return Bank(yaml.safe_load(yaml_content))

def test_speech_routes_to_dtmf_successfully(test_bank, demo_db):
    state = CallState(phase="asking", current_question="Q002_SOMETHING", language="hi", candidates=())
    event = {"speech": "I am a farmer yes", "confidence": 0.9}
    
    with patch("haqdaar.llm.chat") as mock_chat:
        mock_chat.return_value = '{"dtmf_key": "1"}'
        
        new_state, actions = step(state, event, test_bank, demo_db)
        
        # It should have mapped to 1, updated answers with 'something': 'yes', and asked the next question or ended
        assert mock_chat.called
        assert new_state.answers.get("something") == "yes"

def test_speech_routes_to_unclear_when_llm_returns_unclear(test_bank, demo_db):
    state = CallState(phase="asking", current_question="Q002_SOMETHING", language="hi", candidates=())
    event = {"speech": "Blah blah", "confidence": 0.9}
    
    with patch("haqdaar.llm.chat") as mock_chat:
        mock_chat.return_value = '{"dtmf_key": "UNCLEAR"}'
        
        new_state, actions = step(state, event, test_bank, demo_db)
        
        assert mock_chat.called
        # Should NOT update answers
        assert "something" not in new_state.answers
        # Should increment speech attempts
        assert new_state.speech_attempts == 1
        assert "UNCLEAR" in actions[0].get("say", "")
