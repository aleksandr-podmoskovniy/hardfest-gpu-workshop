import importlib.util
import pathlib
import re
import sys
import unittest
import xml.etree.ElementTree as ET

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / (name + ".py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


maths = load("kv_math")
manifests = load("check_manifests")


class Guide(unittest.TestCase):
    def test_readme_is_workshop_with_local_qr_at_top(self):
        readme = (ROOT / "README.md").read_text()
        url = "https://github.com/aleksandr-podmoskovniy/hardfest-gpu-workshop"
        prose = re.sub(r"(?ms)^```[^\n]*\n.*?^```\s*$", "", readme)
        titles = re.findall(r"^# (.+)$", prose, re.M)
        self.assertEqual(len(titles), 1)
        title = titles[0]
        self.assertTrue(title.startswith("Инференс без простоя"))
        self.assertIn("Александр Подмосковный, Флант / Deckhouse Platform", readme)
        top = readme.split("# " + title, 1)[0]
        self.assertIn('src="assets/workshop-qr.svg"', top)
        self.assertIn('<p align="center">', top)
        self.assertIn('width="250" height="250"', top)
        self.assertIn(f'href="{url}"', top)
        for section in ('<a id="contents"></a>', '<a id="setup"></a>',
                        "## 1.", "## 2.", '<a id="cleanup"></a>'):
            self.assertIn(section, readme)
        svg = ET.parse(ROOT / "assets/workshop-qr.svg").getroot()
        self.assertEqual(svg.find("{http://www.w3.org/2000/svg}desc").text, url)
        self.assertEqual(svg.find("{http://www.w3.org/2000/svg}title").text, title)
        self.assertNotIn("<script", (ROOT / "assets/workshop-qr.svg").read_text())

    def test_illustrations_are_local_accessible_and_self_contained(self):
        readme = (ROOT / "README.md").read_text()
        images = re.findall(r"!\[([^]]+)\]\((assets/\d[^)]+\.svg)\)", readme)
        # Validate the illustrations that explain the guide, not a minimum
        # gallery size. Requiring old figures kept redundant content alive.
        self.assertTrue(images)
        for alt, filename in images:
            with self.subTest(filename=filename):
                self.assertGreater(len(alt), 20)
                raw = (ROOT / filename).read_text()
                svg = ET.fromstring(raw)
                self.assertEqual(svg.attrib["viewBox"].split()[2], "1200")
                self.assertIsNotNone(svg.find("{http://www.w3.org/2000/svg}title"))
                self.assertIsNotNone(svg.find("{http://www.w3.org/2000/svg}desc"))
                for node in svg.iter():
                    self.assertNotIn(node.tag.rsplit("}", 1)[-1], ("script", "foreignObject", "image"))
                    self.assertFalse(any(k.rsplit("}", 1)[-1] == "href" for k in node.attrib))

    def test_tune_long_context_is_not_applied_to_strict_cache(self):
        readme = (ROOT / "README.md").read_text()
        ram = readme.split('id="ram"', 1)[1].split('id="speculation"', 1)[0]
        second = readme.split('id="speculation"', 1)[1].split('id="platform"', 1)[0]
        self.assertNotIn("max-model-len: 131072", ram)
        self.assertIn("max-model-len: 131072", second)
        self.assertNotIn("возврат к `16384`", second)
        self.assertRegex(second, r"сравнение \*\*набора настроек\*\*")
        self.assertIn("не измерение вклада одного MTP", second)
        self.assertIn("122 880", second)
        self.assertIn("cpu_bytes_to_use: 34359738368", ram)
        self.assertRegex(ram, r"128 GiB[\s\S]{0,200}A останавливается через Git/Argo")
        for stale in ("gemma-b-128k.yaml", "gemma-b-ram.yaml"):
            self.assertNotIn(stale, readme)

    def test_primary_workshop_uses_gitops_not_private_python_wrappers(self):
        readme = (ROOT / "README.md").read_text()
        for old in ("python3", "scripts/hf.py", ".local/"):
            self.assertNotIn(old, readme)
        for command in ("apply --dry-run=server -f", "git commit -S -s", "git push"):
            self.assertIn(command, (ROOT / "docs/GITOPS.md").read_text())
        self.assertNotIn("kustomize", readme.lower())
        # Copyable equations may use text fences; paragraphs belong in prose.
        for formula in re.findall(r"```text\n(.*?)```", readme, re.S):
            lines = [line for line in formula.splitlines() if line.strip()]
            self.assertLessEqual(len(lines), 3)
            self.assertTrue(all(re.search(r"(?:=|≈)", line) for line in lines))
        gitops = (ROOT / "docs/GITOPS.md").read_text()
        for term in ("ARGO_CONTEXT", "GPU_CONTEXT", '--type merge --patch',
                     r'\"operation\"', r'\"revision\":\"$REVISION\"', r'\"prune\":false'):
            self.assertIn(term, gitops)

    def test_participant_commands_need_no_json_yaml_cli_or_python_wrapper(self):
        paths = [ROOT / "README.md", ROOT / "RTX5060.md", ROOT / "docs/GITOPS.md", ROOT / "docs/SETUP.md",
                 ROOT / "catalog/README.md"] + list((ROOT / "labs").glob("*.md"))
        for path in paths:
            shell = "\n".join(re.findall(r"```(?:bash|sh)\n(.*?)```", path.read_text(), re.S))
            with self.subTest(path=path.relative_to(ROOT)):
                self.assertNotRegex(shell, r"(?m)(?:^|[|;])\s*(?:jq|yq|python3?)(?:\s|$)")

    def test_walkthroughs_do_not_send_readers_to_separate_labs(self):
        self.assertEqual(list((ROOT / "labs").glob("*.md")), [])
        for name in ("README.md", "RTX5060.md"):
            text = (ROOT / name).read_text()
            self.assertNotIn("labs/", text)
            self.assertNotIn("лаборатор", text.lower())
            self.assertIn("<summary>", text)
            self.assertIn("helm template", text)
            self.assertIn("vllm bench serve", text)
            self.assertIn("nvidia-smi mig -lgi", text)

    def test_inline_commands_keep_auth_and_collapsible_sections(self):
        for name in ("README.md", "RTX5060.md"):
            text = (ROOT / name).read_text()
            self.assertEqual(text.count("<details>"), text.count("</details>"))
            self.assertEqual(text.count("<details>"), text.count("<summary>"))
            self.assertIn("IFS= read -r -s MODEL_API_KEY", text)
            self.assertIn("--header @<(printf 'Authorization: Bearer %s", text)
            self.assertNotIn('--header "Authorization: Bearer $', text)
            self.assertIn("unset MODEL_API_KEY", text)

    def test_helm_profiles_are_safe_and_complete(self):
        profiles = list((ROOT / "values").glob("*.yaml"))
        self.assertEqual(len(profiles), 4)
        self.assertEqual(manifests.check(), [])
        for path in profiles:
            with self.subTest(profile=path.stem):
                objects = manifests.render(path)
                resources = objects["Deployment"]
                self.assertIn("checksum/vllm-config", resources["spec"]["template"]["metadata"]["annotations"])
                self.assertEqual(resources["spec"]["replicas"], 0)
                self.assertEqual(resources["spec"]["strategy"]["type"], "Recreate")
                self.assertIn("ResourceClaimTemplate", objects)
                self.assertNotIn("cpu-offload-gb", path.read_text())
                self.assertRegex(resources["spec"]["template"]["spec"]["containers"][0]["image"], r"@sha256:[0-9a-f]{64}")
        for name in ("gemma-a", "gemma-b"):
            self.assertIn("max-model-len: 16384", (ROOT / "values" / (name + ".yaml")).read_text())
        for path in (ROOT / "argocd").glob("*.yaml"):
            self.assertNotIn("automated:", path.read_text())
            self.assertNotIn("finalizers:", path.read_text())

    def test_participant_docs_do_not_contain_speaker_directions(self):
        paths = [ROOT / "README.md", ROOT / "RTX5060.md", ROOT / "WORKSHOP.md"]
        paths += list((ROOT / "labs").glob("*.md"))
        paths += list((ROOT / "docs").glob("*.md"))
        forbidden = re.compile(
            r"\*\*сказать|предложить аудитории|вопрос залу|предъявить аудитории|"
            r"на сцене|до сцены|перед выступлением|ведущего|live-слот|"
            r"^## \d{2}[–-]\d{2}", re.I | re.M)
        for path in paths:
            with self.subTest(path=path.relative_to(ROOT)):
                self.assertIsNone(forbidden.search(path.read_text()))

    def test_workshop_contents_targets_exist_in_readme(self):
        readme = (ROOT / "README.md").read_text()
        anchors = set(re.findall(r'<a id="([^"]+)"></a>', readme))
        targets = re.findall(r"\]\(#([^)]+)\)", readme)
        self.assertTrue(targets)
        self.assertTrue(set(targets).issubset(anchors))
        stages = ['ab', 'monitoring', 'memory', 'ram', 'speculation', 'latency',
                  'placement', 'platform', 'conclusion', 'tp2', 'cleanup', 'setup']
        positions = [readme.index(f'id="{stage}"') for stage in stages]
        self.assertEqual(positions, sorted(positions))
        self.assertEqual(readme.count('<a id="speculation">'), 1)
        self.assertNotIn('Дополнительно. Черновая генерация', readme)

    def test_platform_chapter_describes_recipe_workflow(self):
        readme = (ROOT / "README.md").read_text()
        chapter = readme.split('id="platform"', 1)[1].split('id="conclusion"', 1)[0]
        for term in ("128K", "RAM-offload", "assistant", "hf-platform-gemma", "Gemma A — DP"):
            self.assertIn(term, chapter)
        for term in ("charts/inference-service", "order.enabled: true", "model", "Ready",
                     "helm template"):
            self.assertIn(term, readme)
        self.assertIn("charts/inference-service", chapter)
        self.assertIn("`replicaCount: 0`", chapter)
        self.assertIn("исчезновения Pod", chapter)
        self.assertIn("192 GiB RAM", chapter)
        self.assertRegex(chapter, r"128 GiB[\s\S]{0,100}результат B сохраняется до её остановки")
        self.assertLess(chapter.index("исчезновения Pod"), chapter.index("order.enabled: true"))

    def test_original_teaching_chain_is_preserved_for_current_models(self):
        for name in ("README.md", "RTX5060.md"):
            text = (ROOT / name).read_text()
            for term in ("https://ai.ap4y.ru", "GQA", "TTFT", "max-num-batched-tokens",
                         "reasoning", "CPU", "MTP", "InferenceService"):
                self.assertIn(term, text, name)
            self.assertIn("docs/MEMORY_BUDGET.md#verify-kv", text)
            self.assertLess(text.index('id="ab"'), text.index('id="latency"'))
            self.assertLess(text.index('id="memory"'), text.index('id="ram"'))
        readme = (ROOT / "README.md").read_text()
        for tokens in (131072, 262144):
            for histories, element_bytes in ((1, 2), (8, 2), (8, 1)):
                value = maths.calculate_gemma(tokens=tokens, sessions=histories,
                                              element_bytes=element_bytes)["all_sessions_gib"]
                self.assertIn(str(value).rstrip("0").rstrip(".").replace(".", ",") + " GiB", readme)
        rtx = (ROOT / "RTX5060.md").read_text()
        row = next(line for line in rtx.splitlines() if line.startswith("| 128K |"))
        actual = [float(value) for cell in row.split("|")[2:4]
                  for value in re.findall(r"\d+(?:\.\d+)?", cell)]
        expected = [maths.calculate_gemma_e2b(131072, histories, element_bytes)
                    ["all_sessions_gib"] * 1024
                    for histories in (1, 8) for element_bytes in (2, 1)]
        self.assertEqual(actual, expected)

    def test_workshop_redirects_to_guides_without_a_duplicate_scenario(self):
        alias = (ROOT / "WORKSHOP.md").read_text()
        self.assertIn("](README.md)", alias)
        self.assertIn("](RTX5060.md)", alias)
        self.assertNotIn("## ", alias)
        self.assertNotIn("```", alias)

    def test_qwen_is_a_required_final_stage_with_operational_checks(self):
        readme = (ROOT / "README.md").read_text()
        chapter = readme.split('id="tp2"', 1)[1].split('id="cleanup"', 1)[0]
        self.assertRegex(chapter, r"## 8\.")
        self.assertNotIn("бонус", chapter.lower())
        self.assertLess(readme.index('id="tp2"'), readme.index('id="cleanup"'))
        for term in ("tensor-parallel-size: 2", "acceleratorCount=2",
                     "VK", "MTP", "order.enabled: true", "values/qwen-tp2.yaml",
                     "262144", "122 880", "8192"):
            self.assertIn(term, chapter)
        self.assertIn('helm template hf-qwen-platform', chapter)
        self.assertIn('charts/inference-service', chapter)
        self.assertNotIn('kind: InferenceService', chapter)

    def test_platform_preflight_precedes_releasing_working_gpus(self):
        preflight = (ROOT / "docs/SETUP.md").read_text()
        for term in ("inference-readiness", "до успешной проверки", "authentication: Token"):
            self.assertIn(term, preflight)
        for field in ("acceleratorPolicy.maxAcceleratorCount", "scalingPolicy.minReplicas",
                      "scalingPolicy.maxReplicas", "exposurePolicy.authentication"):
            self.assertIn(field, preflight)
        self.assertIn("не меньше `2`", preflight)
        self.assertIn("оба `1`", preflight)

    def test_long_requests_use_the_ready_tune_profile(self):
        doc = (ROOT / "README.md").read_text()
        extension = doc.split('id="long-context"', 1)[1].split('id="platform"', 1)[0]
        for setting in ("max-model-len", "131072", "Tune", "8192"):
            self.assertIn(setting, extension)
        self.assertIn("Tune уже настроен на 128K", extension)
        self.assertNotIn("max-model-len: 65536", extension)

    def test_kv_visuals_are_in_both_main_stories_before_optimization(self):
        for name, capacity in (("README.md", "25-h100-kv-capacity"),
                               ("RTX5060.md", "26-rtx-kv-capacity")):
            text = (ROOT / name).read_text()
            memory = text.split('id="memory"', 1)[1].split('id="speculation"', 1)[0]
            for asset in ("03-kv-history", "05-kv-reuse", capacity):
                self.assertIn(f"](assets/{asset}.svg)", memory)
            self.assertLess(memory.index("03-kv-history"), memory.index('id="ram"'))
            self.assertLess(memory.index(capacity), memory.index('id="ram"'))

    def test_detailed_client_and_mps_diagnostics_live_in_reference(self):
        for name in ("README.md", "RTX5060.md"):
            text = (ROOT / name).read_text()
            self.assertNotIn("MPS_SERVER_PID=", text)
            self.assertNotIn("export BENCH_DIR=", text)
            self.assertIn("docs/TROUBLESHOOTING.md#a30-claims", text)
            self.assertIn("docs/MEASUREMENTS.md#gemma-client-", text)

    def test_long_output_is_a_separate_check_and_cleanup_preserves_tune_window(self):
        rtx = (ROOT / "RTX5060.md").read_text()
        long_context = rtx.split('id="long-context"', 1)[1].split('id="placement"', 1)[0]
        self.assertIn('--random-output-len "$OUTPUT_TOKENS"', long_context)
        self.assertIn("| Длинный ответ | 122880 | 8192 | `gemma-long-output` |", long_context)
        for variable in ("INPUT_TOKENS", "OUTPUT_TOKENS", "RUN_NAME"):
            self.assertIn(variable, long_context)
        cleanup = rtx.split('id="cleanup"', 1)[1].split('id="setup"', 1)[0]
        self.assertIn("Tune сохраняет окно 128K", cleanup)
        self.assertNotIn("0.90", cleanup)
        h100 = (ROOT / "README.md").read_text()
        self.assertIn("| Длинный ответ | 122 880 | 8192 | 1 / 1 |", h100)

    def test_chat_path_is_present_from_manual_to_platform_stages(self):
        readme = (ROOT / "README.md").read_text()
        self.assertLess(readme.index('id="chat"'), readme.index('id="setup"'))
        self.assertIn("Gemma A — Base", readme)
        self.assertIn("Gemma B — Tune", readme)
        self.assertIn("маршрут", readme)
        self.assertIn("Bifrost", readme)
        self.assertIn("через AI Inference", readme)
        self.assertIn("docs/CHAT_AND_ACCESS.md", readme)
        guide = (ROOT / "docs/CHAT_AND_ACCESS.md").read_text()
        for term in ("pending", "Virtual Key", "OIDC", "MCP", "ACL", "отзыв"):
            self.assertIn(term, guide)

    def test_baseline_leads_to_explanation_before_final_summary(self):
        readme = (ROOT / "README.md").read_text()
        ordered = ["ab", "monitoring", "latency", "conclusion", "setup"]
        positions = [readme.index(f'<a id="{name}"></a>') for name in ordered]
        self.assertEqual(positions, sorted(positions))
        self.assertIn('<a id="topology"></a>', readme)
        self.assertIn('<a id="contents"></a>', readme)

    def test_both_walkthroughs_start_with_prepared_running_base(self):
        for name in ("README.md", "RTX5060.md"):
            text = (ROOT / name).read_text()
            with self.subTest(name=name):
                baseline = text.split('id="memory"', 1)[0]
                intro = " ".join(re.sub(r"[*`]", "", baseline).split())
                self.assertRegex(intro, r"Gemma A — Base.{0,16}(?:уже )?(?:запущена|работает|отвечает)")
                self.assertIn("Helm", baseline)
                self.assertNotIn("replicaCount: 1", baseline)
                self.assertNotIn("helm template", baseline)
                self.assertLess(text.index('id="conclusion"'), text.index('id="cleanup"'))
                self.assertLess(text.index('id="cleanup"'), text.index('id="setup"'))
                self.assertIn("https://ai.ap4y.ru", baseline)

    def test_initial_launch_commands_live_in_preparation(self):
        setup = (ROOT / "docs/SETUP.md").read_text()
        self.assertIn("## 6. Заранее запустить A", setup)
        self.assertIn("Prepare running Gemma baseline", setup)
        rtx = setup.split('id="rtx"', 1)[1]
        self.assertIn("replicaCount: 1", rtx)
        self.assertIn("rtx-gemma-base", rtx)
        self.assertIn("заказы — `order.enabled: false`", rtx)

    def test_a30_is_prepared_before_placement_and_creation_is_explained_later(self):
        for name in ("README.md", "RTX5060.md"):
            with self.subTest(guide=name):
                text = (ROOT / name).read_text()
                setup = text.split('id="setup"', 1)[1]
                self.assertIn("Qwen", setup)
                self.assertIn("A30", setup)
                self.assertRegex(setup, r"отвеча")
                self.assertRegex(setup, r"выключен")
                self.assertIn("(#a30-orders)", text)
                self.assertLess(text.index('id="platform"'), text.index('id="a30-orders"'))
                self.assertRegex(text, r"(?:части существовали раньше|Sync готовых частей не доказывает их создание)")

    def test_readme_has_no_decorative_tagline_or_boilerplate_labels(self):
        readme = (ROOT / "README.md").read_text()
        self.assertNotIn("·", readme)
        for phrase in ("практический мастер-класс ·", "Выберите учебный документ",
                       "**Ожидаемый результат:**", "**Вывод:**", "**Задача:**",
                       "Откройте мастер-класс на телефоне"):
            self.assertNotIn(phrase, readme)

    def test_reference_docs_have_no_duplicate_workshop_or_editorial_pages(self):
        self.assertEqual({path.name for path in (ROOT / "docs").rglob("*.md")}, {
            "SETUP.md", "GITOPS.md", "MEMORY_BUDGET.md", "MEASUREMENTS.md",
            "OBSERVABILITY.md", "CHAT_AND_ACCESS.md", "TROUBLESHOOTING.md", "SOURCES.md",
        })

    def test_gemma_math_uses_global_kv_heads_and_sliding_window(self):
        for tokens, bf16 in ((131072, 10.78125), (133120, 10.9375), (262144, 20.78125)):
            with self.subTest(tokens=tokens):
                self.assertEqual(maths.calculate_gemma(tokens=tokens)["one_session_gib"], bf16)
                self.assertEqual(maths.calculate_gemma(tokens=tokens, element_bytes=1)["one_session_gib"], bf16 / 2)
        self.assertEqual(maths.calculate_gemma(tokens=512)["sliding_attention_gib"], .390625)
        self.assertEqual(maths.calculate_gemma(tokens=262144)["sliding_attention_gib"], .78125)
        self.assertEqual(maths.calculate_gemma(tokens=133120, sessions=8, element_bytes=1)["all_sessions_gib"], 43.75)

    def test_platform_illustration_matches_the_long_context_target(self):
        svg = (ROOT / "assets/09-platform.svg").read_text()
        self.assertIn("Gemma: целевой профиль", svg)
        self.assertIn("128K, FP8 KV", svg)
        self.assertIn("RAM offload, chunked prefill, MTP", svg)
        self.assertNotIn("64K", svg)

    def test_all_theory_diagrams_are_local_svg_without_external_content(self):
        diagrams = list((ROOT / "assets").glob("[0-9][0-9]-*.svg"))
        from build_diagrams import BUILDERS
        self.assertEqual(len(diagrams), len(BUILDERS))
        docs = "\n".join(path.read_text() for path in [ROOT / "README.md", ROOT / "RTX5060.md"] +
                         list((ROOT / "docs").rglob("*.md")) +
                         list((ROOT / "labs").glob("*.md")))
        for path in diagrams:
            svg = ET.parse(path).getroot()
            self.assertEqual(svg.attrib["viewBox"], "0 0 1200 760")
            self.assertEqual(svg.attrib["data-design"], "hardfest-v2")
            self.assertIn(path.name, docs, f"Unreferenced illustration: {path.name}")
            for node in svg.iter():
                self.assertNotIn(node.tag.rsplit("}", 1)[-1], ("script", "foreignObject", "image"))

    def test_shell_examples_are_parseable(self):
        docs = load("check_docs")
        count, errors = docs.check()
        self.assertGreater(count, 40)
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
