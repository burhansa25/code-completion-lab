"""No network calls or real HJ binaries. Unit + mocked API contract tests."""
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from benchmark_v2 import cases, cli, engine, providers, scoring


class CaseFixture(unittest.TestCase):
    def make_c(self, root, *, byte0=False):
        source = root / "hello.c"
        prefix = "\n".join(f"line_{i};" for i in range(14)) + "\n    printf"
        reference = '("Hello");'
        suffix = "\n    return 0;\n}"
        if byte0:
            prefix = ""
        full = prefix + reference + suffix
        source.write_text(full, encoding="utf-8")
        cursor = len(prefix.encode())
        return {"schema_version": 1, "status": "prepared", "case_id": "c1", "language": "c",
                "source_relative": "hello.c", "source_path": str(source),
                "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                "prefix": prefix, "cursor_byte": cursor, "candidate_count": 3,
                "top_candidates": [{"structure": "ID", "frequency": 10},
                                   {"structure": "NUM", "frequency": 7},
                                   {"structure": "STR", "frequency": 3}],
                "references": [{"structure": "ID", "text": reference, "start_byte": cursor,
                                "end_byte_from_text": cursor+len(reference.encode()),
                                "retrieval_rank": 1}]}

    def test_c_last10_and_oracle(self):
        with tempfile.TemporaryDirectory() as d:
            x = self.make_c(Path(d))
            y = cases.prepare_case(x)
            self.assertEqual(y["planned_api_requests"], 9)
            self.assertEqual(len(y["conditions"]), 5)
            self.assertEqual(y["conditions"][-1]["condition"], "ideal_guidance")
            self.assertEqual(y["conditions"][-1]["repetitions"], 3)
            self.assertEqual(y["conditions"][-1]["structure"], "ID")
            self.assertNotIn("line_0", y["conditions"][0]["prompt"])
            self.assertIn("line_13", y["conditions"][0]["prompt"])
            self.assertNotIn('("Hello");', y["conditions"][-1]["prompt"])
            self.assertEqual(len(x["top_candidates"]), 3)
            self.assertEqual(y["completion_mode"], "prefix")
            self.assertEqual(len(y["candidate_pool"]), 3)

    def test_c_full_prefix_and_fim(self):
        with tempfile.TemporaryDirectory() as d:
            x = self.make_c(Path(d))
            full = cases.prepare_case(x, context="full")
            self.assertIn("line_0", full["conditions"][0]["prompt"])
            infill = cases.prepare_case(x, context="full", completion="infill")
            from benchmark_v2.prompts import PREFIX_SUFFIX
            self.assertTrue(infill["conditions"][0]["prompt"].startswith(PREFIX_SUFFIX))
            self.assertIn("SUFFIX START", infill["conditions"][0]["prompt"])
            self.assertIn("return 0;", infill["conditions"][0]["prompt"])
            self.assertIn('("Hello");', x["references"][0]["text"])
            self.assertNotIn('("Hello");', infill["conditions"][0]["prompt"])


    def test_cli_prepared_cases_integrity(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            c = self.make_c(root)
            path = root / "cases.jsonl"
            new = cases.prepare_case(c)
            cli.safe_write_cases(path, [new], {"source": "hello.c"})
            self.assertEqual(len(cli.verify_prepared_cases(path)), 1)
            path.write_text(path.read_text()+" ")
            with self.assertRaisesRegex(ValueError, "modified"):
                cli.verify_prepared_cases(path)

    def test_frozen_c_fim_prompt_and_source_slice(self):
        from benchmark_v2.prompts import PREFIX_SUFFIX
        from prepare_c_batch import make_infill
        import copy
        with tempfile.TemporaryDirectory() as d:
            case = self.make_c(Path(d))
            case["conditions"] = [{"condition": "without_guidance", "structure": None, "prompt": ""}]
            case["conditions"] += [{"condition": f"guided_rank_{idx}", "structure": item["structure"], "prompt": ""}
                                   for idx, item in enumerate(case["top_candidates"], 1)]
            old = make_infill(copy.deepcopy(case), Path(case["source_path"]).read_bytes(), case["references"][0])
            new = cases.prepare_case(case, context="full", completion="infill", profile="lab")
            for previous, current in zip(old["conditions"], new["conditions"]):
                self.assertEqual(previous["prompt"], current["prompt"])
            self.assertTrue(new["conditions"][0]["prompt"].startswith(PREFIX_SUFFIX))

    def test_c_source_integrity(self):
        with tempfile.TemporaryDirectory() as d:
            x = self.make_c(Path(d))
            Path(x["source_path"]).write_text("tampered", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "source modified"):
                cases.prepare_case(x)

    def test_smallbasic_mapped_ideal(self):
        with tempfile.TemporaryDirectory() as d:
            mapping_path = Path(d) / "token_mapping.json"
            mapping_path.write_text(json.dumps({"Stmt_token1": {"type": "STRING", "content": "("}}))
            sb = {"status": "prepared", "case_id": "sb1", "language": "smallbasic",
                  "source_relative": "file.sb", "cursor_byte": 80,
                  "prefix": "TextWindow.WriteLine", "candidate_count": 3,
                  "top_candidates": [{"structure": "Stmt_token1 Exprs", "readable_structure": "( Exprs", "frequency": 5},
                                     {"structure": "NUM", "readable_structure": "NUM", "frequency": 3},
                                     {"structure": "STR", "readable_structure": "STR", "frequency": 1}],
                  "references": [{"structure": "Stmt_token1 Exprs", "text": '("Hello")'}],
                  "token_mapping_path": str(mapping_path),
                  "token_mapping_sha256": hashlib.sha256(mapping_path.read_bytes()).hexdigest()}
            mapper = types.ModuleType("map_hj_candidates")
            mapper.map_structure = lambda s, mapping: (s.replace("Stmt_token1", "("), [])
            with patch.dict(sys.modules, {"map_hj_candidates": mapper}):
                y = cases.prepare_case(sb)
            self.assertEqual(y["conditions"][-1]["structure"], "( Exprs")
            self.assertEqual(y["planned_api_requests"], 9)
            self.assertEqual(y["conditions"][0]["repetitions"], 3)
            self.assertEqual(y["conditions"][1]["repetitions"], 1)

    def test_top20_pool_retrieval(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            case = self.make_c(root)
            db = {"42": [{"key": f"C{i}", "value": 100-i} for i in range(20)]}
            data = json.dumps(db).encode()
            f = root / "candidates.json"; f.write_bytes(data)
            case["states"] = [42]
            case["candidate_count"] = 20
            case["top_candidates"] = [{"structure": f"C{i}", "frequency": 100-i} for i in range(3)]
            case["provenance"] = {"database_path": str(f), "database_sha256": hashlib.sha256(data).hexdigest()}
            y = cases.prepare_case(case, top_k=20)
            self.assertEqual(len(y["candidate_pool"]), 20)
            self.assertEqual(y["planned_api_requests"], 26)
            self.assertEqual(len(y["conditions"]), 22)
            self.assertEqual(len(y["top_candidates"]), 3)

    def test_top20_sha_guard(self):
        with tempfile.TemporaryDirectory() as d:
            case = self.make_c(Path(d))
            case["candidate_count"] = 20
            f = Path(d)/"db.json"; f.write_text('{}')
            case["provenance"] = {"database_path": str(f), "database_sha256": "incorrect"}
            with self.assertRaisesRegex(ValueError, "changed since preparation"):
                cases.prepare_case(case, top_k=20)


class EngineTests(unittest.TestCase):
    def basic(self):
        return {"case_id": "case1", "status": "prepared", "cursor_byte": 12,
                "source_relative": "test.sb", "references": [{"text": "abc", "structure": "ID"}],
                "prompt_profile": "v2_lab", "experiment_version": "v2-rq2-1",
                "conditions": [{"condition": "without_guidance", "structure": None, "prompt": "prefix", "repetitions": 3},
                               {"condition": "guided_rank_1", "structure": "ID", "prompt": "guided1", "repetitions": 1},
                               {"condition": "guided_rank_2", "structure": "NUM", "prompt": "guided2", "repetitions": 1},
                               {"condition": "guided_rank_3", "structure": "STR", "prompt": "guided3", "repetitions": 1},
                               {"condition": "ideal_guidance", "structure": "ID", "prompt": "ideal", "repetitions": 3}]}

    def test_provider_payload_token_parameter(self):
        _, xai = providers.resolve_model("grok")
        _, oai = providers.resolve_model("gpt4o")
        _, groq = providers.resolve_model("gptoss")
        x = providers.payload_for(xai, "hi", .5, 512)
        a = providers.payload_for(oai, "hi", .5, 512)
        g = providers.payload_for(groq, "hi", .5, 512)
        self.assertEqual(x["max_tokens"], 512)
        self.assertNotIn("max_completion_tokens", x)
        self.assertEqual(a["max_completion_tokens"], 512)
        self.assertEqual(g["max_completion_tokens"], 512)
        self.assertEqual(g["reasoning_effort"], "low")

    def test_model_alias_and_count(self):
        name, config = providers.resolve_model("gpt")
        self.assertEqual(name, "gpt4o")
        self.assertEqual(config["id"], "gpt-4o")
        _, model = providers.resolve_model("grok")
        tasks = engine.build_tasks([self.basic()], model, temperature=0.5, max_tokens=512)
        self.assertEqual(len(tasks), 9)
        self.assertEqual(len({x["task_id"] for x in tasks}), 9)

    def test_generation_resume_and_integrity(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            path = root / "cases.jsonl";path.write_text(json.dumps(self.basic())+"\n")
            run_dir = root / "run"
            _, model = providers.resolve_model("grok")
            config = {"temperature": 0.5, "max_tokens": 512, "delay": 0}
            sent = []
            def fake_api(_model, body):
                sent.append(body)
                return {"choices": [{"message": {"content": "abc"}, "finish_reason": "stop"}]}
            mock_rows = lambda records, require_bleu=True: scoring.score_records(records, require_bleu=False)
            with patch.dict(os.environ, {"XAI_API_KEY": "fake-key"}), patch.object(engine, "score_records", mock_rows):
                engine.run_one_group(run_dir, path, model, config, yes=True, api_fn=fake_api)
                self.assertEqual(len(sent), 9)
                engine.run_one_group(run_dir, path, model, config, yes=True, api_fn=fake_api)
                self.assertEqual(len(sent), 9)
                self.assertEqual(len((run_dir / "responses.jsonl").read_text().splitlines()), 9)
                # Detect modified scores even though a CSV exists.
                (run_dir / "scores.csv").write_text('malicious data')
                with self.assertRaisesRegex(ValueError, "scores modified"):
                    engine.run_one_group(run_dir, path, model, config, yes=True, api_fn=fake_api)

    def test_resume_after_partial_api_failure(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); source=root/"cases.jsonl";source.write_text(json.dumps(self.basic())+"\n")
            dest=root/"run";_, model=providers.resolve_model("gpt4o")
            config={"temperature":0.5,"max_tokens":512,"delay":0}
            sent=[]
            def flakey(_model,body):
                sent.append(body)
                if len(sent)==3: raise RuntimeError("HTTP temporary")
                return {"choices":[{"message":{"content":"abc"},"finish_reason":"stop"}]}
            mock_rows=lambda records,require_bleu=True: scoring.score_records(records,require_bleu=False)
            with patch.dict(os.environ,{"OPENAI_API_KEY":"fake"}),patch.object(engine,"score_records",mock_rows):
                with self.assertRaisesRegex(RuntimeError,"Generation incomplete"):
                    engine.run_one_group(dest,source,model,config,yes=True,api_fn=flakey)
                good=engine.read_success(dest,engine.read_manifest(dest));self.assertEqual(len(good),2)
                engine.run_one_group(dest,source,model,config,yes=True,api_fn=flakey)
                self.assertEqual(len(sent),10)  # two successes, one failed, seven retries
                self.assertEqual(len(engine.read_success(dest,engine.read_manifest(dest))),9)

    def test_prompt_changes_change_task_identity(self):
        _,model=providers.resolve_model("grok")
        case=self.basic()
        original=engine.build_tasks([case],model,temperature=.5,max_tokens=512)
        case["conditions"][0]["prompt"]="CHANGED"
        updated=engine.build_tasks([case],model,temperature=.5,max_tokens=512)
        self.assertNotEqual(set(x["task_id"] for x in original),set(x["task_id"] for x in updated))

    def test_empty_response_scored_as_failure(self):
        _, model=providers.resolve_model("grok")
        item=engine.build_tasks([self.basic()],model,temperature=.5,max_tokens=512)[0]
        row={**item, "content":"", "status":"empty_completion", "latency_seconds":0.1}
        scored=scoring.score_records([row],require_bleu=False)
        self.assertEqual(scored[0]["exact_match_strip"],0)
        self.assertEqual(scored[0]["generation_status"],"empty_completion")

    def test_cli_smoke_prefers_nonzero(self):
        def item(byte):
            return {"status":"prepared","cursor_byte":byte,"references":[{}],"top_candidates":[1,2,3],"candidate_count":3}
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            a=root/"a.jsonl";a.write_text(json.dumps(item(0))+"\n")
            b=root/"b.jsonl";b.write_text(json.dumps(item(10))+"\n")
            result=cli.select_cases("c",[("a",a),("b",b)],"smoke",None,3)
            self.assertEqual(result[0][1][0]["cursor_byte"],10)


if __name__ == "__main__":
    unittest.main()
