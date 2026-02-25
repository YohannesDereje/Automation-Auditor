from __future__ import annotations

import re
import tempfile
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

try:
	from docling.document_converter import DocumentConverter
except ImportError:
	DocumentConverter = None

try:
	import fitz
except ImportError:
	fitz = None


class RubricDimensions(BaseModel):
	objectives: str = Field(default="")
	deliverables: str = Field(default="")
	constraints: str = Field(default="")


class ForensicChunk(BaseModel):
	chunk_id: str
	header: str
	header_level: int
	content: str
	page_numbers: list[int] = Field(default_factory=list)
	table_metadata: list[dict[str, Any]] = Field(default_factory=list)


class DocumentForensics:
	def __init__(self) -> None:
		self._markdown_text: str = ""
		self._json_payload: dict[str, Any] = {}
		self._chunks: list[ForensicChunk] = []

	@property
	def chunks(self) -> list[ForensicChunk]:
		return list(self._chunks)

	def ingest(self, pdf_path: str) -> dict[str, Any]:
		path = Path(pdf_path)
		if not path.exists():
			return self._error_payload("FileNotFoundError", f"PDF file not found: {pdf_path}")
		if not path.is_file():
			return self._error_payload("FileNotFoundError", f"PDF path is not a file: {pdf_path}")

		if DocumentConverter is None:
			return self._error_payload(
				"ImportError",
				"Docling is not installed. Run 'uv add docling' and retry.",
			)

		try:
			converter = DocumentConverter()
			result = converter.convert(str(path))
			markdown_text = DocAnalyst._extract_markdown(result)
			json_payload = DocAnalyst._extract_json(result)

			self._markdown_text = markdown_text
			self._json_payload = json_payload
			self._chunks = self.chunk_markdown_by_headers(markdown_text, json_payload)

			return {
				"success": True,
				"pdf_path": str(path),
				"markdown": markdown_text,
				"json": json_payload,
				"chunks": [chunk.model_dump() for chunk in self._chunks],
			}
		except Exception as error:
			return self._error_payload(
				"CorruptedOrUnreadablePDF",
				f"Failed to parse PDF with Docling: {error}",
			)

	def chunk_markdown_by_headers(
		self,
		markdown_text: str,
		json_payload: dict[str, Any] | None = None,
	) -> list[ForensicChunk]:
		if not markdown_text.strip():
			return []

		payload = json_payload or {}
		global_page_numbers = self._extract_page_numbers(markdown_text, payload)
		table_metadata = self._extract_table_metadata(payload)

		lines = markdown_text.splitlines()
		chunks: list[ForensicChunk] = []
		current_header = "Document Preamble"
		current_level = 0
		current_lines: list[str] = []
		chunk_index = 0

		def flush_current_chunk() -> None:
			nonlocal chunk_index, current_header, current_level, current_lines
			content = "\n".join(current_lines).strip()
			if not content:
				return

			local_pages = self._extract_page_numbers(content, payload)
			selected_pages = local_pages if local_pages else global_page_numbers
			chunk_index += 1
			chunks.append(
				ForensicChunk(
					chunk_id=f"chunk_{chunk_index:03d}",
					header=current_header,
					header_level=current_level,
					content=content,
					page_numbers=selected_pages,
					table_metadata=table_metadata,
				)
			)

		header_regex = re.compile(r"^(#{1,2})\s+(.*)$")

		for line in lines:
			match = header_regex.match(line.strip())
			if match:
				flush_current_chunk()
				current_level = len(match.group(1))
				current_header = match.group(2).strip()
				current_lines = []
				continue
			current_lines.append(line)

		flush_current_chunk()
		return chunks

	def search_sections(self, query: str) -> list[dict[str, Any]]:
		if not query or not query.strip():
			return []
		if not self._chunks:
			return []

		keywords = [
			word.lower()
			for word in re.findall(r"[a-zA-Z0-9_]+", query)
			if len(word) > 2
		]
		if not keywords:
			return []

		scored: list[tuple[int, ForensicChunk]] = []
		for chunk in self._chunks:
			haystack = f"{chunk.header}\n{chunk.content}".lower()
			score = sum(haystack.count(keyword) for keyword in keywords)
			if score > 0:
				scored.append((score, chunk))

		scored.sort(key=lambda item: item[0], reverse=True)
		return [item[1].model_dump() for item in scored]

	@staticmethod
	def _extract_page_numbers(
		text: str,
		json_payload: dict[str, Any] | None = None,
	) -> list[int]:
		page_numbers = {
			int(match)
			for match in re.findall(r"\b(?:page|p\.)\s*(\d{1,4})\b", text, flags=re.IGNORECASE)
		}

		payload = json_payload or {}
		for key, value in DocumentForensics._walk_dict(payload):
			if key.lower() in {"page", "page_no", "page_num", "page_number"}:
				if isinstance(value, int):
					page_numbers.add(value)
				elif isinstance(value, str) and value.isdigit():
					page_numbers.add(int(value))

		return sorted(number for number in page_numbers if number > 0)

	@staticmethod
	def _extract_table_metadata(json_payload: dict[str, Any]) -> list[dict[str, Any]]:
		tables: list[dict[str, Any]] = []
		for key, value in DocumentForensics._walk_dict(json_payload):
			if "table" not in key.lower():
				continue

			if isinstance(value, dict):
				tables.append(
					{
						"key": key,
						"rows": value.get("rows") or value.get("row_count"),
						"cols": value.get("cols") or value.get("column_count"),
						"page": value.get("page")
						or value.get("page_no")
						or value.get("page_number"),
					}
				)
			elif isinstance(value, list):
				tables.append({"key": key, "entries": len(value)})

		return tables

	@staticmethod
	def _walk_dict(payload: Any) -> list[tuple[str, Any]]:
		items: list[tuple[str, Any]] = []

		def walk(node: Any, prefix: str = "") -> None:
			if isinstance(node, dict):
				for key, value in node.items():
					full_key = f"{prefix}.{key}" if prefix else str(key)
					items.append((full_key, value))
					walk(value, full_key)
			elif isinstance(node, list):
				for index, value in enumerate(node):
					walk(value, f"{prefix}[{index}]")

		walk(payload)
		return items

	def _error_payload(self, error_type: str, message: str) -> dict[str, Any]:
		return {
			"success": False,
			"error_type": error_type,
			"error": message,
			"markdown": "",
			"json": {},
			"chunks": [],
		}


class DocAnalyst:
	def __init__(self) -> None:
		self._pdf_path: Path | None = None
		self._markdown_text: str = ""
		self._json_payload: dict[str, Any] = {}
		self._dimensions = RubricDimensions()
		self._image_output_dirs: list[str] = []
		self._forensics = DocumentForensics()

	def load_requirements(self, pdf_path: str) -> dict[str, Any]:
		path = Path(pdf_path)
		result = self._forensics.ingest(pdf_path)
		if not result.get("success", False):
			error_type = result.get("error_type", "DoclingError")
			error_message = result.get("error", "Unknown PDF parsing error")
			if error_type == "FileNotFoundError":
				raise FileNotFoundError(error_message)
			raise RuntimeError(error_message)

		self._pdf_path = path
		self._markdown_text = result.get("markdown", "")
		self._json_payload = result.get("json", {})
		return {
			"pdf_path": str(path),
			"markdown": self._markdown_text,
			"json": self._json_payload,
			"chunks": result.get("chunks", []),
		}

	def search_sections(self, query: str) -> list[dict[str, Any]]:
		return self._forensics.search_sections(query)

	def extract_rubric_dimensions(self) -> RubricDimensions:
		if not self._markdown_text:
			raise RuntimeError(
				"No requirements loaded. Call load_requirements(pdf_path) first."
			)

		self._dimensions = RubricDimensions(
			objectives=self._extract_section("objectives"),
			deliverables=self._extract_section("deliverables"),
			constraints=self._extract_section("constraints"),
		)
		return self._dimensions

	def query_requirements(self, query: str) -> str:
		if not query or not query.strip():
			raise ValueError("Query cannot be empty.")
		if not self._markdown_text:
			raise RuntimeError(
				"No requirements loaded. Call load_requirements(pdf_path) first."
			)

		normalized_query = query.lower()
		sections = {
			"objectives": self._dimensions.objectives,
			"deliverables": self._dimensions.deliverables,
			"constraints": self._dimensions.constraints,
		}

		if not any(sections.values()):
			self.extract_rubric_dimensions()
			sections = {
				"objectives": self._dimensions.objectives,
				"deliverables": self._dimensions.deliverables,
				"constraints": self._dimensions.constraints,
			}

		keywords = [word for word in re.findall(r"[a-zA-Z0-9_]+", normalized_query) if len(word) > 2]
		best_section_name = ""
		best_section_text = ""
		best_score = 0

		for section_name, section_text in sections.items():
			if not section_text:
				continue

			section_lower = section_text.lower()
			score = sum(section_lower.count(keyword) for keyword in keywords)

			if section_name in normalized_query:
				score += 3

			if score > best_score:
				best_score = score
				best_section_name = section_name
				best_section_text = section_text

		if best_score > 0 and best_section_text:
			snippet = self._truncate_snippet(best_section_text)
			return f"[{best_section_name.upper()}] {snippet}"

		markdown_lower = self._markdown_text.lower()
		sentence_candidates = re.split(r"(?<=[.!?])\s+", self._markdown_text)
		scored_sentences: list[tuple[int, str]] = []

		for sentence in sentence_candidates:
			sentence_lower = sentence.lower()
			score = sum(sentence_lower.count(keyword) for keyword in keywords)
			if score > 0:
				scored_sentences.append((score, sentence.strip()))

		if scored_sentences:
			scored_sentences.sort(key=lambda item: item[0], reverse=True)
			return self._truncate_snippet(scored_sentences[0][1])

		if normalized_query in markdown_lower:
			index = markdown_lower.index(normalized_query)
			start = max(0, index - 200)
			end = min(len(self._markdown_text), index + 400)
			return self._truncate_snippet(self._markdown_text[start:end])

		return "No strongly relevant requirement snippet found for the given query."

	def extract_images_from_pdf(self, pdf_path: str) -> list[str]:
		path = Path(pdf_path)
		if not path.exists():
			raise FileNotFoundError(f"PDF file not found: {pdf_path}")
		if not path.is_file():
			raise FileNotFoundError(f"PDF path is not a file: {pdf_path}")

		if fitz is None:
			raise RuntimeError(
				"PyMuPDF is not installed. Run 'uv add pymupdf' and retry."
			)

		image_paths: list[str] = []
		output_dir: str | None = None

		try:
			with fitz.open(str(path)) as document:
				for page_index in range(document.page_count):
					page = document.load_page(page_index)
					images = page.get_images(full=True)

					if not images:
						continue

					if output_dir is None:
						output_dir = tempfile.mkdtemp(prefix="docanalyst_images_")
						self._image_output_dirs.append(output_dir)

					for image_index, image_info in enumerate(images, start=1):
						xref = image_info[0]
						image_data = document.extract_image(xref)
						image_bytes = image_data.get("image")
						extension = image_data.get("ext", "png")

						if not image_bytes:
							continue

						filename = (
							f"page_{page_index + 1:03d}_img_{image_index:03d}.{extension}"
						)
						file_path = Path(output_dir) / filename
						file_path.write_bytes(image_bytes)
						image_paths.append(str(file_path))

			return image_paths
		except FileNotFoundError:
			raise
		except Exception as error:
			raise RuntimeError(f"Failed to extract images from PDF: {error}") from error

	@staticmethod
	def _truncate_snippet(text: str, max_chars: int = 700) -> str:
		clean = re.sub(r"\s+", " ", text).strip()
		if len(clean) <= max_chars:
			return clean
		return clean[:max_chars].rstrip() + "..."

	def _extract_section(self, section_title: str) -> str:
		escaped_title = re.escape(section_title)
		heading_pattern = re.compile(
			rf"(^|\n)\s{{0,3}}#{1,6}\s*{escaped_title}\b.*?$",
			flags=re.IGNORECASE | re.MULTILINE,
		)
		match = heading_pattern.search(self._markdown_text)
		if not match:
			return ""

		section_start = match.end()
		next_heading = re.search(
			r"\n\s{0,3}#{1,6}\s+",
			self._markdown_text[section_start:],
			flags=re.MULTILINE,
		)
		section_end = (
			section_start + next_heading.start()
			if next_heading
			else len(self._markdown_text)
		)
		section_body = self._markdown_text[section_start:section_end]
		return self._truncate_snippet(section_body, max_chars=2000)

	@staticmethod
	def _extract_markdown(result: Any) -> str:
		if hasattr(result, "document"):
			document = getattr(result, "document")
			if hasattr(document, "export_to_markdown"):
				value = document.export_to_markdown()
				if isinstance(value, str):
					return value
			if hasattr(document, "to_markdown"):
				value = document.to_markdown()
				if isinstance(value, str):
					return value

		if hasattr(result, "markdown") and isinstance(result.markdown, str):
			return result.markdown
		if hasattr(result, "text") and isinstance(result.text, str):
			return result.text
		if isinstance(result, str):
			return result

		return str(result)

	@staticmethod
	def _extract_json(result: Any) -> dict[str, Any]:
		if hasattr(result, "document"):
			document = getattr(result, "document")
			if hasattr(document, "export_to_dict"):
				value = document.export_to_dict()
				if isinstance(value, dict):
					return value
			if hasattr(document, "model_dump"):
				value = document.model_dump()
				if isinstance(value, dict):
					return value

		if hasattr(result, "model_dump"):
			value = result.model_dump()
			if isinstance(value, dict):
				return value

		if isinstance(result, dict):
			return result

		return {"raw_result": str(result)}
