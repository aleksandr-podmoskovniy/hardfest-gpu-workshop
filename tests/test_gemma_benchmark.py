import json
import os
from pathlib import Path
import subprocess
import tempfile
import textwrap
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]


class GemmaBenchmark(unittest.TestCase):
    def setUp(self):
        self.job = yaml.safe_load((ROOT / "examples/gemma-benchmark-job.yaml").read_text())
        self.pod = self.job["spec"]["template"]["spec"]
        self.container = self.pod["containers"][0]
        self.env = {item["name"]: item for item in self.container["env"]}
        self.script = self.container["args"][0]

    def test_client_is_suspended_bounded_cpu_only(self):
        self.assertEqual(self.job["kind"], "Job")
        self.assertTrue(self.job["spec"]["suspend"])
        self.assertEqual(self.job["spec"]["backoffLimit"], 0)
        self.assertGreater(self.job["spec"]["activeDeadlineSeconds"], 0)
        self.assertGreater(self.job["spec"]["ttlSecondsAfterFinished"], 0)
        self.assertEqual(self.pod["restartPolicy"], "Never")
        self.assertFalse(self.pod["automountServiceAccountToken"])
        self.assertEqual(self.pod["nodeSelector"]["kubernetes.io/hostname"], "REPLACE_CPU_NODE")
        self.assertNotIn("resourceClaims", self.pod)
        self.assertNotIn("claims", self.container["resources"])
        for part in ("requests", "limits"):
            self.assertEqual(set(self.container["resources"][part]), {"cpu", "memory", "ephemeral-storage"})
        self.assertEqual(self.pod["volumes"], [{"name": "work", "emptyDir": {"sizeLimit": "6Gi"}}])

    def test_client_has_no_privilege_or_host_access(self):
        security = self.pod["securityContext"]
        self.assertTrue(security["runAsNonRoot"])
        self.assertNotEqual(security["runAsUser"], 0)
        self.assertEqual(security["seccompProfile"]["type"], "RuntimeDefault")
        self.assertFalse(self.container["securityContext"]["allowPrivilegeEscalation"])
        self.assertEqual(self.container["securityContext"]["capabilities"]["drop"], ["ALL"])
        for field in ("hostNetwork", "hostPID", "hostIPC"):
            self.assertFalse(self.pod.get(field, False))

    def test_authentication_uses_separate_secret_refs(self):
        refs = []
        for name in ("MANUAL_API_KEY", "PLATFORM_API_KEY", "HF_TOKEN"):
            self.assertNotIn("value", self.env[name])
            ref = self.env[name]["valueFrom"]["secretKeyRef"]
            if name == "HF_TOKEN":
                self.assertTrue(ref["name"].startswith("REPLACE_"))
            else:
                self.assertRegex(ref["name"], r"^replace-[a-z0-9-]+$")
            self.assertTrue(ref["key"].startswith("REPLACE_"))
            self.assertEqual(ref.get("optional", False), name != "HF_TOKEN")
            refs.append(ref["name"])
        self.assertEqual(len(set(refs)), 3)
        self.assertNotIn("OPENAI_API_KEY", self.env)
        self.assertNotIn("--api-key", self.script)
        self.assertNotIn("set -x", self.script)

    def test_image_and_shared_tokenizer_are_pinned(self):
        lock = json.loads((ROOT / "models.lock.json").read_text())
        self.assertEqual(self.container["image"], lock["runtime"]["image"])
        self.assertEqual(lock["runtime"]["version"], "0.31.0")
        self.assertEqual(self.env["TOKENIZER_ID"]["value"], lock["models"]["gemma"]["repository"])
        self.assertEqual(self.env["TOKENIZER_REVISION"]["value"], lock["models"]["gemma"]["revision"])
        self.assertIn("--include 'tokenizer*' 'config.json' 'special_tokens_map.json' 'chat_template*'", self.script)
        self.assertNotIn("safetensors", self.script)

    def test_h100_workload_is_fixed_and_shell_is_valid(self):
        self.assertEqual(self.env["BENCH_TARGET"]["value"], "both")
        expected = {"INPUT_TOKENS": "8192", "OUTPUT_TOKENS": "2048", "NUM_PROMPTS": "8", "CONCURRENCY": "4"}
        for name, value in expected.items():
            self.assertEqual(self.env[name]["value"], value)
        self.assertEqual(self.container["command"], ["/bin/bash", "-euc"])
        result = subprocess.run(["bash", "-n"], input=self.script, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def run_mocked_job(self, **overrides):
        """Exercise the manifest shell; no Kubernetes, network, vLLM or GPU access."""
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            bin_dir = work / "bin"
            bin_dir.mkdir()
            stub = textwrap.dedent("""\
                #!/usr/bin/env python3
                import json
                import os
                from pathlib import Path
                import sys

                program = Path(sys.argv[0]).name
                args = sys.argv[1:]
                record = {"program": program, "args": args,
                          "api_key": os.environ.get("OPENAI_API_KEY"),
                          "hf_token_present": bool(os.environ.get("HF_TOKEN"))}
                with Path("calls.jsonl").open("a") as stream:
                    stream.write(json.dumps(record) + "\\n")
                if program == "vllm":
                    def option(name):
                        return args[args.index(name) + 1]
                    prompts = int(option("--num-prompts"))
                    failed = int(os.environ.get("MOCK_FAILED", "0"))
                    result = {"completed": prompts - failed, "failed": failed,
                              "total_input_tokens": prompts * int(option("--random-input-len")),
                              "total_output_tokens": prompts * int(option("--random-output-len")),
                              "max_concurrency": int(option("--max-concurrency")),
                              "model_id": option("--model")}
                    result_path = Path(option("--result-dir")) / option("--result-filename")
                    result_path.write_text(json.dumps(result))
                """)
            for name in ("hf", "vllm"):
                executable = bin_dir / name
                executable.write_text(stub)
                executable.chmod(0o755)
            env = os.environ.copy()
            env.update({name: item["value"] for name, item in self.env.items() if "value" in item})
            env.update({"PATH": str(bin_dir) + os.pathsep + os.environ["PATH"],
                        "PLATFORM_MODEL": "platform-served-model",
                        "HF_TOKEN": "test-hf-credential",
                        "PLATFORM_API_KEY": "test-platform-credential",
                        "OPENAI_API_KEY": "must-not-be-inherited"})
            env.pop("MANUAL_API_KEY", None)
            for name, value in overrides.items():
                if value is None:
                    env.pop(name, None)
                else:
                    env[name] = value
            result = subprocess.run(["bash", "-euc", self.script], cwd=work, env=env,
                                    text=True, capture_output=True, timeout=20)
            log = work / "calls.jsonl"
            calls = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
            results = {path.name: json.loads(path.read_text()) for path in (work / "results").glob("*.json")}
            return result, calls, results

    def test_four_separate_results_share_workload_and_use_per_target_auth(self):
        result, calls, results = self.run_mocked_job()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([call["program"] for call in calls], ["hf", "vllm", "vllm", "vllm", "vllm"])
        self.assertTrue(calls[0]["hf_token_present"])
        benchmark_calls = calls[1:]
        filenames = ["manual-first.json", "manual-repeat.json", "platform-first.json", "platform-repeat.json"]
        self.assertEqual(set(results), set(filenames))
        tokenizers = set()
        workloads = []
        for index, call in enumerate(benchmark_calls):
            args = call["args"]
            options = {args[position]: args[position + 1] for position in range(len(args) - 1)
                       if args[position].startswith("--") and not args[position + 1].startswith("--")}
            self.assertEqual(options["--result-filename"], filenames[index])
            self.assertEqual(options["--seed"], "42")
            self.assertEqual(options["--random-range-ratio"], "0")
            self.assertEqual(options["--ready-check-timeout-sec"], "0")
            self.assertEqual(options["--num-warmups"], "0")
            self.assertEqual(options["--endpoint"], "/v1/completions")
            self.assertIn("--ignore-eos", args)
            self.assertIn("--save-detailed", args)
            self.assertEqual(options["--temperature"], "0")
            tokenizers.add(options["--tokenizer"])
            workloads.append(tuple(options[name] for name in ("--random-input-len", "--random-output-len",
                                                               "--num-prompts", "--max-concurrency")))
            is_manual = index < 2
            self.assertEqual(call["api_key"], None if is_manual else "test-platform-credential")
            self.assertEqual(options["--model"], "gemma-4-31b" if is_manual else "platform-served-model")
            self.assertEqual(options["--base-url"], self.env["MANUAL_BASE_URL" if is_manual else "PLATFORM_BASE_URL"]["value"])
        self.assertEqual(len(tokenizers), 1)
        self.assertEqual(set(workloads), {("8192", "2048", "8", "4")})
        for target in ("manual", "platform"):
            for series in ("first", "repeat"):
                begin = f"RESULT_JSON_BEGIN target={target} pass={series}\n"
                end = f"\nRESULT_JSON_END target={target} pass={series}"
                raw = result.stdout.split(begin, 1)[1].split(end, 1)[0]
                self.assertEqual(json.loads(raw), results[f"{target}-{series}.json"])
        for credential in ("test-hf-credential", "test-platform-credential", "must-not-be-inherited"):
            self.assertNotIn(credential, result.stdout + result.stderr)

    def test_optional_manual_key_is_not_reused_for_platform(self):
        result, calls, _ = self.run_mocked_job(MANUAL_API_KEY="test-manual-credential")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([call["api_key"] for call in calls[1:]],
                         ["test-manual-credential"] * 2 + ["test-platform-credential"] * 2)
        self.assertNotIn("test-manual-credential", result.stdout + result.stderr)

    def test_missing_required_credentials_or_model_placeholder_stop_before_download(self):
        for override in ({"HF_TOKEN": ""}, {"PLATFORM_API_KEY": ""},
                         {"PLATFORM_MODEL": "REPLACE_PLATFORM_SERVED_MODEL_ID"}):
            with self.subTest(override=override):
                result, calls, results = self.run_mocked_job(**override)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(calls, [])
                self.assertEqual(results, {})

    def test_manual_only_needs_no_platform_configuration_or_key(self):
        result, calls, results = self.run_mocked_job(
            BENCH_TARGET="manual", PLATFORM_BASE_URL=None, PLATFORM_MODEL=None, PLATFORM_API_KEY=None)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([call["program"] for call in calls], ["hf", "vllm", "vllm"])
        self.assertEqual(set(results), {"manual-first.json", "manual-repeat.json"})
        self.assertTrue(all(call["api_key"] is None for call in calls[1:]))

    def test_platform_only_needs_no_manual_configuration_or_key(self):
        result, calls, results = self.run_mocked_job(
            BENCH_TARGET="platform", MANUAL_BASE_URL=None, MANUAL_MODEL=None, MANUAL_API_KEY=None)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([call["program"] for call in calls], ["hf", "vllm", "vllm"])
        self.assertEqual(set(results), {"platform-first.json", "platform-repeat.json"})
        self.assertTrue(all(call["api_key"] == "test-platform-credential" for call in calls[1:]))

    def test_invalid_target_stops_before_download(self):
        result, calls, results = self.run_mocked_job(BENCH_TARGET="unknown")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("BENCH_TARGET must be", result.stderr)
        self.assertEqual(calls, [])
        self.assertEqual(results, {})

    def test_each_selected_target_fails_closed_on_missing_configuration(self):
        overrides = [
            {"BENCH_TARGET": "manual", "MANUAL_MODEL": None},
            {"BENCH_TARGET": "manual", "MANUAL_BASE_URL": None},
            {"BENCH_TARGET": "platform", "PLATFORM_MODEL": None},
            {"BENCH_TARGET": "platform", "PLATFORM_BASE_URL": None},
            {"BENCH_TARGET": "platform", "PLATFORM_API_KEY": None},
        ]
        for override in overrides:
            with self.subTest(override=override):
                result, calls, results = self.run_mocked_job(**override)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(calls, [])
                self.assertEqual(results, {})

    def test_failed_series_is_exported_but_does_not_continue_or_report_success(self):
        result, calls, results = self.run_mocked_job(MOCK_FAILED="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual([call["program"] for call in calls], ["hf", "vllm"])
        self.assertEqual(set(results), {"manual-first.json"})
        self.assertIn("RESULT_JSON_END target=manual pass=first", result.stdout)
        self.assertIn("Incomplete benchmark", result.stderr)


if __name__ == "__main__":
    unittest.main()
