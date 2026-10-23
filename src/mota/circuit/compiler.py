"""Circuit compiler entry point for the C3 hierarchy fixtures."""

from __future__ import annotations

from mota.circuit.hierarchy import HierarchyProject, compile_hierarchy
from mota.ir.models_v2 import CircuitCompileResult
from mota.schematic.layout import layout_hierarchy


def compile_circuit(project: HierarchyProject) -> CircuitCompileResult:
    """Flatten hierarchy, then lay the resulting circuit onto pages.

    The returned object binds the CircuitIR, multi-page schematic, diagnostics,
    instance paths and page map to one compile result; callers must not mix
    artifacts from two results.
    """
    result = compile_hierarchy(project)
    schematic = layout_hierarchy(
        result.circuit, result.page_records, result.page_by_component,
        result.unit_views, locked_pages=project.locked_pages)
    return result.model_copy(update={"schematic": schematic})


def compile_report(result: CircuitCompileResult) -> str:
    """Human-reviewable page index plus located hierarchy diagnostics."""
    lines = ["# Hierarchy compile report", ""]
    lines.append(f"- semantic hash: `{result.semantic_hash}`")
    lines.append(f"- components: {len(result.circuit.components)}")
    lines.append(f"- nets: {len(result.circuit.nets)}")
    lines.append(f"- pages: {len(result.schematic.pages)}")
    lines.append(f"- diagnostics: {len(result.diagnostics)}")
    lines.append("")
    lines.append("## Pages")
    for page in result.schematic.pages:
        ports = ", ".join(port["name"] for port in page.ports) or "-"
        lines.append(f"- {page.page_index:02d} `{page.page_id}` "
                     f"{page.instance_path} · symbols {len(page.symbols)} · "
                     f"ports {ports}")
    lines.append("")
    lines.append("## Diagnostics")
    if not result.diagnostics:
        lines.append("- none")
    else:
        for diag in result.diagnostics:
            location = diag.instance_path or "-"
            target = f" ({diag.object_ref})" if diag.object_ref else ""
            lines.append(f"- `{diag.code}` {diag.severity} @ {location}"
                         f"{target}: {diag.message}")
    return "\n".join(lines) + "\n"
