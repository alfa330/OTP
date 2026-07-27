# -*- coding: utf-8 -*-
"""Контракт множественной загрузки баз лидов TEZ в интерфейсе."""

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PANEL_PATH = ROOT / "src" / "components" / "salary" / "TezLeadsPanel.jsx"
SOURCE = PANEL_PATH.read_text(encoding="utf-8-sig")


class TezLeadsMultiUploadUiTests(unittest.TestCase):
    def test_files_are_uploaded_sequentially_with_independent_results(self):
        start = SOURCE.index("const upload = useCallback(async () => {")
        end = SOURCE.index("\n  const recompute =", start)
        upload = SOURCE[start:end]

        self.assertIn("Array.from(fileRef.current?.files || [])", upload)
        self.assertIn("TEZ_LEADS_MAX_FILES_PER_UPLOAD", upload)
        self.assertIn("for (let index = 0; index < files.length; index += 1)", upload)
        self.assertIn("form.append('file', file)", upload)
        self.assertIn("form.append('year', year)", upload)
        self.assertIn("form.append('month', monthNum)", upload)
        self.assertIn("const resp = await axios.post(", upload)
        self.assertNotIn("Promise.all", upload)
        self.assertIn("results.push({", upload)
        self.assertIn("ok: false", upload)
        self.assertIn("setUploadResults([...results])", upload)
        self.assertIn("source_file_name: file.name", upload)
        self.assertIn("setInvalidRows(invalid)", upload)
        self.assertIn("fileRef.current.value = ''", upload)

    def test_file_input_and_progress_support_multiple_files(self):
        start = SOURCE.index("ref={fileRef}")
        end = SOURCE.index("/>", start)
        file_input = SOURCE[start:end]

        self.assertIn('type="file"', file_input)
        self.assertIn("multiple", file_input)
        self.assertIn("disabled={uploading}", file_input)
        self.assertIn("uploadProgress.current", SOURCE)
        self.assertIn("uploadProgress.total", SOURCE)
        self.assertIn("'Загрузить базы'", SOURCE)
        self.assertIn("Результаты загрузки", SOURCE)

    def test_one_poll_tracks_all_successful_batches(self):
        start = SOURCE.index("const pollBatches = useCallback(")
        end = SOURCE.index("\n\n  useEffect(() => () => {", start)
        polling = SOURCE[start:end]

        self.assertIn("new Set((batchIds || []).filter(Boolean))", polling)
        self.assertIn("clearTimeout(pollRef.current)", polling)
        self.assertIn("pollBatches(ids, attempt + 1, scopeKey, generation)", polling)
        self.assertIn("batch?.check_status === 'error'", polling)
        self.assertIn("scopeKey !== activeScopeRef.current", polling)
        self.assertIn("generation !== pollGenerationRef.current", polling)
        self.assertIn("pollBatchIdsRef.current.clear()", polling)

        upload_start = SOURCE.index("const upload = useCallback(async () => {")
        upload_end = SOURCE.index("\n  const recompute =", upload_start)
        upload = SOURCE[upload_start:upload_end]
        self.assertIn("if (result.batchId) batchIds.push(result.batchId)", upload)
        self.assertIn(
            "batchIds.forEach((batchId) => pollBatchIdsRef.current.add(batchId))",
            upload,
        )
        self.assertIn("const activeBatchIds = [...pollBatchIdsRef.current]", upload)
        self.assertEqual(
            upload.count("pollBatches(activeBatchIds, 0, uploadScope, pollGeneration)"),
            1,
        )

    def test_stale_lead_requests_cannot_replace_a_new_scope(self):
        start = SOURCE.index("const loadLeads = useCallback(")
        end = SOURCE.index("\n\n  useEffect(() => {", start)
        loader = SOURCE[start:end]

        self.assertIn("const requestId = ++leadsRequestRef.current", loader)
        self.assertIn("const requestScope = statsScopeKey", loader)
        self.assertGreaterEqual(
            loader.count("requestId !== leadsRequestRef.current"),
            2,
        )
        self.assertGreaterEqual(
            loader.count("requestScope !== activeScopeRef.current"),
            2,
        )


if __name__ == "__main__":
    unittest.main()
