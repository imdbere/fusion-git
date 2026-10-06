"""Operations on Fusion designs: the wrapper component, metadata, export and import."""

import os
import re
import shutil
import tempfile
import time
import uuid

import adsk.core
import adsk.fusion

from .log import UserError

ATTR_GROUP = "fusiongit"
FORMAT_VERSION = "1"

_app = adsk.core.Application.get()
_own_saves = 0


# --- documents ---------------------------------------------------------------

def active_design():
    product = _app.activeProduct
    return adsk.fusion.Design.cast(product) if product else None


def lineage(doc):
    """Cloud id of the document, stable across versions. None until it is known: right after
    the first save Fusion reports a local cache path here until the upload has finished."""
    try:
        file_id = doc.dataFile.id if doc and doc.isSaved and doc.dataFile else None
    except RuntimeError:
        return None
    return file_id if file_id and file_id.startswith("urn:") else None


def save(doc, description):
    """Save without triggering the commit-on-save prompt."""
    global _own_saves
    _own_saves += 1
    if not doc.save(description):
        _own_saves -= 1
        raise UserError("Fusion could not save the design.")


def save_as(doc, name, folder, description, timeout_s=120):
    global _own_saves
    _own_saves += 1
    if not doc.saveAs(name, folder, description, ""):
        _own_saves -= 1
        raise UserError("Fusion could not save the design.")
    end = time.time() + timeout_s
    while not doc.isSaved:
        if time.time() > end:
            raise UserError("Timed out waiting for Fusion to save the design.")
        adsk.doEvents()
        time.sleep(0.2)


def consume_own_save():
    """True if the save that just finished was started by the add-in."""
    global _own_saves
    if _own_saves > 0:
        _own_saves -= 1
        return True
    return False


# --- metadata ----------------------------------------------------------------

def _set_attr(attributes, name, value):
    existing = attributes.itemByName(ATTR_GROUP, name)
    if existing:
        existing.value = value
    else:
        attributes.add(ATTR_GROUP, name, value)


def get_metadata(design):
    """Repo binding stored in the design (travels with the cloud document), or None."""
    attributes = design.attributes
    path = attributes.itemByName(ATTR_GROUP, "pathInRepo")
    if not path:
        return None
    remote = attributes.itemByName(ATTR_GROUP, "remoteUrl")
    link_id = attributes.itemByName(ATTR_GROUP, "linkId")
    return {"pathInRepo": path.value, "remoteUrl": remote.value if remote else "",
            "linkId": link_id.value if link_id else ""}


def set_metadata(design, path_in_repo, remote_url, link_id=None):
    _set_attr(design.attributes, "pathInRepo", path_in_repo)
    _set_attr(design.attributes, "remoteUrl", remote_url or "")
    _set_attr(design.attributes, "formatVersion", FORMAT_VERSION)
    if link_id:
        _set_attr(design.attributes, "linkId", link_id)


def new_link_id():
    return uuid.uuid4().hex


def link_key(design):
    """Key of the design's entry in links.json. A design's cloud id changes after its first
    upload, so the add-in stores its own id in the design; older links used the cloud id."""
    meta = get_metadata(design)
    return (meta and meta["linkId"]) or lineage(design.parentDocument)


def design_of(doc):
    try:
        product = doc.products.itemByProductType("DesignProductType")
    except (RuntimeError, AttributeError):
        return None
    return adsk.fusion.Design.cast(product) if product else None


# --- structure ---------------------------------------------------------------

def ensure_parametric(design):
    if design.designType != adsk.fusion.DesignTypes.ParametricDesignType:
        design.designType = adsk.fusion.DesignTypes.ParametricDesignType


def is_part_design(design):
    try:
        return design.designIntent == adsk.fusion.DesignIntentTypes.PartDesignIntentType
    except (AttributeError, RuntimeError):
        return False


def allow_components(design, force_hybrid=False):
    """Part designs can only hold one component; the wrapper needs a Hybrid design
    (Hybrid rather than Assembly, so bodies can still be modelled directly)."""
    try:
        types = adsk.fusion.DesignIntentTypes
        intent = design.designIntent
    except (AttributeError, RuntimeError):
        return
    if intent == types.PartDesignIntentType or (force_hybrid and intent != types.HybridDesignIntentType):
        try:
            design.designIntent = types.HybridDesignIntentType
        except RuntimeError:
            pass
    if design.designIntent == types.PartDesignIntentType:
        raise UserError("This is a Part design, which can only contain a single component. Change it to "
                        "a Hybrid design in the document settings, then try again.")


def wrapper_occurrence(design):
    for occ in design.rootComponent.occurrences:
        if occ.component.attributes.itemByName(ATTR_GROUP, "wrapper"):
            return occ
    return None


def content_outside_wrapper(design):
    """Human-readable list of root-level content that a commit would not include."""
    root = design.rootComponent
    wrapper = wrapper_occurrence(design)
    found = []
    others = [o.name for o in root.occurrences if not wrapper or o.entityToken != wrapper.entityToken]
    if others:
        found.append("components: " + ", ".join(others))
    for label, count in (("bodies", root.bRepBodies.count), ("sketches", root.sketches.count),
                         ("features", root.features.count),
                         ("construction planes", root.constructionPlanes.count),
                         ("joints", root.joints.count + root.asBuiltJoints.count)):
        if count:
            found.append(f"{count} {label}")
    return found


def external_references(design):
    return [o for o in design.rootComponent.allOccurrences if o.isReferencedComponent]


def break_external_links(design):
    """Turn every external reference into a local component (nested ones included)."""
    for _ in range(20):
        refs = external_references(design)
        if not refs:
            return
        if not any(_try(o.breakLink) for o in refs):
            break
    if external_references(design):
        raise UserError("Some external references could not be converted to local components.")


def _try(fn):
    try:
        return fn()
    except RuntimeError:
        return False


def delete_orphan_user_parameters(design):
    """Delete user parameters nothing depends on (they are not synced anyway)."""
    while True:
        orphans = [p for p in design.userParameters if p.isDeletable and p.dependentParameters.count == 0]
        if not orphans or not any(_try(p.deleteMe) for p in orphans):
            return


def import_wrapper(design, file_path):
    """Import an f3d into the root as the wrapper component (named after the file).
    Imports from a temporary copy, because Fusion unpacks the archive into an _XRef_
    folder next to the file it imports."""
    im = _app.importManager
    temp_copy = os.path.join(tempfile.mkdtemp(prefix="fusiongit-"), os.path.basename(file_path))
    shutil.copyfile(file_path, temp_copy)
    try:
        result = im.importToTarget2(im.createFusionArchiveImportOptions(temp_copy), design.rootComponent)
    except RuntimeError as e:
        raise UserError(f"Fusion could not import {os.path.basename(file_path)} ({e}). "
                        "The design was not changed.") from e
    occs = [adsk.fusion.Occurrence.cast(r) for r in result] if result else []
    occs = [o for o in occs if o]
    if len(occs) != 1:
        raise UserError(f"Importing {os.path.basename(file_path)} produced {len(occs)} components, expected 1.")
    occ = occs[0]
    _set_attr(occ.component.attributes, "wrapper", "1")
    return occ


def replace_contents(design, file_path):
    """Make the design consist of exactly the imported file.

    The new version is imported before anything is deleted, so a failed import
    leaves the design untouched. Then the old timeline is removed: the import's
    timeline group is moved to the front and everything after it is deleted."""
    ensure_parametric(design)
    allow_components(design)
    root = design.rootComponent
    timeline = design.timeline
    old_count = timeline.count
    old_params = {p.name for p in design.userParameters}
    old_occurrences = list(root.occurrences)
    old_bodies = list(root.bRepBodies)

    occ = import_wrapper(design, file_path)
    new_params = {p.name for p in design.userParameters} - old_params

    if old_count:
        imported = occ.timelineObject
        item = imported.parentGroup if imported and imported.parentGroup else imported
        if item and item.isGroup:
            adsk.fusion.TimelineGroup.cast(item).isCollapsed = True
        if item and item.canReorder(0) and item.reorder(0):
            timeline.markerPosition = 1
            timeline.deleteAllAfterMarker()
            timeline.moveToEnd()
        else:
            for index in reversed(range(old_count)):
                old = timeline.item(index)
                if old.isGroup:
                    _try(lambda: adsk.fusion.TimelineGroup.cast(old).deleteMe(True))
                else:
                    _try(old.deleteObject)
    for leftover in old_occurrences + old_bodies:
        if leftover.isValid:
            _try(leftover.deleteMe)

    delete_orphan_user_parameters(design)
    _restore_parameter_names(design, new_params)
    occ.activate()
    return occ


def _restore_parameter_names(design, imported_names):
    """Imported parameters that clashed with the old ones got a suffix (width -> width_1);
    once the old ones are gone, give them their names back."""
    for name in imported_names:
        match = re.fullmatch(r"(.+)_(\d+)", name)
        param = design.userParameters.itemByName(name)
        if match and param and not design.userParameters.itemByName(match.group(1)):
            _try(lambda p=param, n=match.group(1): setattr(p, "name", n))


def wrap_existing(design, component_name):
    """Init: move the whole design into one wrapper component named `component_name`.
    The imported component takes its name from the file, hence the temp file name."""
    ensure_parametric(design)
    allow_components(design)
    em = design.exportManager
    temp_file = os.path.join(tempfile.mkdtemp(prefix="fusiongit-"), component_name + ".f3d")
    if not em.execute(em.createFusionArchiveExportOptions(temp_file)):
        raise UserError("Exporting the design failed. The design was not changed.")
    return replace_contents(design, temp_file)


# --- export ------------------------------------------------------------------

def export_wrapper(design, occ, file_path, exports):
    """Export the wrapper to `file_path` plus the extra formats enabled in `exports`.
    Returns all written files."""
    em = design.exportManager
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    if not em.execute(em.createFusionArchiveExportOptions(file_path, occ.component)):
        raise UserError("Exporting the design failed.")
    written = [file_path]
    stem = os.path.splitext(file_path)[0]
    if exports.get("step"):
        em.execute(em.createSTEPExportOptions(stem + ".step", occ.component))
        written.append(stem + ".step")
    if exports.get("stl"):
        em.execute(em.createSTLExportOptions(occ.component, stem + ".stl"))
        written.append(stem + ".stl")
    if exports.get("thumbnail") and _app.activeViewport:
        if _app.activeViewport.saveAsImageFile(stem + ".png", 512, 512):
            written.append(stem + ".png")
    return [p for p in written if os.path.exists(p)]


def open_preview(file_path, title):
    """Open an f3d as a new, unsaved document (for comparing versions)."""
    im = _app.importManager
    doc = im.importToNewDocument(im.createFusionArchiveImportOptions(file_path))
    if doc:
        doc.name = title
    return doc
