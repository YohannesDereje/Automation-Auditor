from __future__ import annotations

import ast
import inspect
import tempfile
from pathlib import Path
from typing import Any

from git import Repo
from git.exc import GitCommandError, InvalidGitRepositoryError, NoSuchPathError


class GraphAstVisitor(ast.NodeVisitor):
	def __init__(self, file_path: str) -> None:
		self.file_path = file_path
		self.class_names: list[str] = []
		self.function_names: list[str] = []
		self.stategraph_instantiations: list[dict[str, Any]] = []
		self.add_node_calls: list[dict[str, Any]] = []
		self.add_edge_calls: list[dict[str, Any]] = []
		self.conditional_edge_calls: list[dict[str, Any]] = []

	def visit_ClassDef(self, node: ast.ClassDef) -> None:
		self.class_names.append(node.name)
		self.generic_visit(node)

	def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
		self.function_names.append(node.name)
		self.generic_visit(node)

	def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
		self.function_names.append(node.name)
		self.generic_visit(node)

	def visit_Call(self, node: ast.Call) -> None:
		if isinstance(node.func, ast.Name) and node.func.id == "StateGraph":
			self.stategraph_instantiations.append(
				{
					"file": self.file_path,
					"line": node.lineno,
					"column": node.col_offset,
				}
			)

		if isinstance(node.func, ast.Attribute):
			method_name = node.func.attr
			receiver_name = (
				node.func.value.id if isinstance(node.func.value, ast.Name) else None
			)

			if method_name == "add_node":
				node_name: str | None = None
				if node.args and isinstance(node.args[0], ast.Constant):
					if isinstance(node.args[0].value, str):
						node_name = node.args[0].value
				self.add_node_calls.append(
					{
						"file": self.file_path,
						"line": node.lineno,
						"column": node.col_offset,
						"builder": receiver_name,
						"node_name": node_name,
					}
				)

			if method_name == "add_edge":
				self.add_edge_calls.append(
					{
						"file": self.file_path,
						"line": node.lineno,
						"column": node.col_offset,
						"builder": receiver_name,
					}
				)

			if method_name == "add_conditional_edges":
				self.conditional_edge_calls.append(
					{
						"file": self.file_path,
						"line": node.lineno,
						"column": node.col_offset,
						"builder": receiver_name,
					}
				)

		self.generic_visit(node)


class RepoManager:
	def __init__(self) -> None:
		self._temp_dir: tempfile.TemporaryDirectory[str] | None = None
		self._repo_path: Path | None = None

	@property
	def repo_path(self) -> str | None:
		return str(self._repo_path) if self._repo_path else None

	def close(self) -> None:
		if self._temp_dir is not None:
			self._temp_dir.cleanup()
			self._temp_dir = None
			self._repo_path = None

	def __enter__(self) -> "RepoManager":
		return self

	def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
		self.close()

	def clone_repo(self, repo_url: str) -> str:
		if not repo_url or not repo_url.strip():
			raise ValueError("Repository URL cannot be empty.")

		self.close()
		self._temp_dir = tempfile.TemporaryDirectory()
		destination = Path(self._temp_dir.name) / "target_repo"

		try:
			Repo.clone_from(repo_url, destination)
			self._repo_path = destination
			return str(destination)
		except GitCommandError as error:
			error_message = str(error).lower()
			if "authentication" in error_message or "could not read" in error_message:
				raise RuntimeError(
					"Authentication failed while cloning repository. "
					"Check credentials or repository visibility."
				) from error
			raise RuntimeError(f"Failed to clone repository: {error}") from error
		except Exception as error:
			raise RuntimeError(f"Unexpected error during clone: {error}") from error

	def extract_git_history(self, path: str, limit: int = 20) -> list[dict[str, str]]:
		try:
			repo = Repo(path)
			commits = list(repo.iter_commits(max_count=limit))
			history: list[dict[str, str]] = []

			for commit in commits:
				history.append(
					{
						"hash": commit.hexsha[:10],
						"author": commit.author.name,
						"message": commit.message.strip(),
						"timestamp": commit.committed_datetime.isoformat(),
					}
				)

			return history
		except InvalidGitRepositoryError as error:
			raise RuntimeError(f"Not a valid git repository: {path}") from error
		except NoSuchPathError as error:
			raise RuntimeError(f"Repository path does not exist: {path}") from error
		except GitCommandError as error:
			raise RuntimeError(f"Unable to read git history: {error}") from error
		except Exception as error:
			raise RuntimeError(f"Unexpected git history error: {error}") from error

	def get_file_tree(self, path: str) -> list[str]:
		base_path = Path(path)
		if not base_path.exists():
			raise FileNotFoundError(f"Path does not exist: {path}")

		files: list[str] = []
		for file_path in base_path.rglob("*"):
			if file_path.is_dir():
				continue

			parts = set(file_path.parts)
			if ".git" in parts or "__pycache__" in parts:
				continue

			files.append(str(file_path.relative_to(base_path)))

		files.sort()
		return files

	def analyze_graph_structure(self, path: str) -> dict[str, Any]:
		base_path = Path(path)
		if not base_path.exists():
			raise FileNotFoundError(f"Path does not exist: {path}")

		python_files = [
			base_path / relative_path
			for relative_path in self.get_file_tree(path)
			if relative_path.endswith(".py")
		]

		all_classes: dict[str, list[str]] = {}
		all_functions: dict[str, list[str]] = {}
		detected_components: dict[str, list[dict[str, Any]]] = {
			"stategraph_instantiations": [],
			"add_node_calls": [],
			"add_edge_calls": [],
			"add_conditional_edges_calls": [],
		}
		parse_errors: list[dict[str, str]] = []

		for python_file in python_files:
			try:
				source = python_file.read_text(encoding="utf-8")
				tree = ast.parse(source, filename=str(python_file))
				visitor = GraphAstVisitor(str(python_file.relative_to(base_path)))
				visitor.visit(tree)

				all_classes[str(python_file.relative_to(base_path))] = visitor.class_names
				all_functions[str(python_file.relative_to(base_path))] = (
					visitor.function_names
				)

				detected_components["stategraph_instantiations"].extend(
					visitor.stategraph_instantiations
				)
				detected_components["add_node_calls"].extend(visitor.add_node_calls)
				detected_components["add_edge_calls"].extend(visitor.add_edge_calls)
				detected_components["add_conditional_edges_calls"].extend(
					visitor.conditional_edge_calls
				)
			except SyntaxError as error:
				parse_errors.append(
					{
						"file": str(python_file.relative_to(base_path)),
						"error": f"SyntaxError: {error.msg} (line {error.lineno})",
					}
				)
			except UnicodeDecodeError as error:
				parse_errors.append(
					{
						"file": str(python_file.relative_to(base_path)),
						"error": f"UnicodeDecodeError: {error}",
					}
				)
			except Exception as error:
				parse_errors.append(
					{
						"file": str(python_file.relative_to(base_path)),
						"error": f"Unexpected parse error: {error}",
					}
				)

		return {
			"summary": {
				"python_files_scanned": len(python_files),
				"classes_found": sum(len(item) for item in all_classes.values()),
				"functions_found": sum(len(item) for item in all_functions.values()),
				"stategraph_detected": bool(
					detected_components["stategraph_instantiations"]
				),
				"node_definitions_detected": bool(detected_components["add_node_calls"]),
			},
			"classes": all_classes,
			"functions": all_functions,
			"langgraph_components": detected_components,
			"errors": parse_errors,
		}

	def get_repo_summary(
		self,
		repo_url: str,
		ignore_patterns: list[str] | None = None,
	) -> str:
		if not repo_url or not repo_url.strip():
			raise ValueError("Repository URL cannot be empty.")

		patterns = ignore_patterns or [
			".git",
			"node_modules",
			"dist",
			"build",
			"coverage",
			"__pycache__",
			".venv",
			".mypy_cache",
			".pytest_cache",
		]

		try:
			from gitingest import ingest
		except ImportError as error:
			raise RuntimeError(
				"gitingest is not installed. Run 'uv add gitingest' and retry."
			) from error

		try:
			summary_result = self._call_gitingest_ingest(
				ingest_func=ingest,
				repo_url=repo_url,
				ignore_patterns=patterns,
			)
			summary_text = self._extract_summary_text(summary_result)
			if not summary_text.strip():
				raise RuntimeError("gitingest returned an empty summary.")
			return summary_text
		except Exception as error:
			raise RuntimeError(f"Failed to generate repository summary: {error}") from error

	@staticmethod
	def _call_gitingest_ingest(
		ingest_func: Any,
		repo_url: str,
		ignore_patterns: list[str],
	) -> Any:
		candidates: list[dict[str, Any]] = [
			{"repo_url": repo_url, "ignore_patterns": ignore_patterns},
			{"url": repo_url, "ignore_patterns": ignore_patterns},
			{"repo": repo_url, "ignore_patterns": ignore_patterns},
			{"repo_url": repo_url, "exclude_patterns": ignore_patterns},
			{"url": repo_url, "exclude_patterns": ignore_patterns},
			{"repo": repo_url, "exclude_patterns": ignore_patterns},
		]

		signature = inspect.signature(ingest_func)
		accepts_kwargs = any(
			parameter.kind == inspect.Parameter.VAR_KEYWORD
			for parameter in signature.parameters.values()
		)

		for kwargs in candidates:
			filtered_kwargs = kwargs
			if not accepts_kwargs:
				filtered_kwargs = {
					key: value
					for key, value in kwargs.items()
					if key in signature.parameters
				}

			if not filtered_kwargs:
				continue

			try:
				return ingest_func(**filtered_kwargs)
			except TypeError:
				continue

		return ingest_func(repo_url)

	@staticmethod
	def _extract_summary_text(summary_result: Any) -> str:
		if isinstance(summary_result, str):
			return summary_result

		if isinstance(summary_result, dict):
			for key in (
				"summary",
				"content",
				"text",
				"report",
				"result",
				"output",
			):
				value = summary_result.get(key)
				if isinstance(value, str):
					return value
			return str(summary_result)

		for attr in ("summary", "content", "text", "report", "result", "output"):
			if hasattr(summary_result, attr):
				value = getattr(summary_result, attr)
				if isinstance(value, str):
					return value

		return str(summary_result)
