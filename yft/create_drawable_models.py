import bpy
from typing import Dict, Optional
from collections import defaultdict
from ..tools.blenderhelper import create_mesh_object
from ..cwxml.drawable import Geometry, Drawable, Bone, DrawableModel
from ..sollumz_properties import LODLevel, SollumType, SOLLUMZ_UI_NAMES
from ..ydr.properties import DrawableModelProperties
from ..lods import LODLevels
from .geometry_data import GeometryData, VertexAttributes, MeshBuilder, split_indices

# TODO: Make ydr use this module

LODsByGroup = Dict[str, dict[LODLevel, GeometryData]]
GeomDataByBone = Dict[int, dict[LODLevel, GeometryData]]
GeomsByBone = Dict[int, dict[LODLevel, list[Geometry]]]


def create_drawable_models(drawable_xml: Drawable, materials: list[bpy.types.Material], drawable_obj: bpy.types.Object):
    """Create fragment mesh joining all skinned geometries. Any non-skinned meshes will be split and parented to their corresponding bone. Returns all mesh objects."""
    models = create_non_skinned_models(
        drawable_xml, materials, drawable_obj)

    set_lod_model_properties(models, drawable_xml)

    if not has_skinned_models(drawable_xml):
        return models

    skinned_model = create_skinned_model(
        drawable_xml, materials, drawable_obj)

    set_skinned_model_properties(drawable_obj, drawable_xml)

    return [skinned_model, *models]


def create_drawable_models_split_by_group(drawable_xml: Drawable, materials: list[bpy.types.Material], drawable_obj: bpy.types.Object):
    """Create fragment mesh split by vertex groups. Any non-skinned meshes will be split and parented to their corresponding bone. Returns all mesh objects."""
    models = create_non_skinned_models(
        drawable_xml, materials, drawable_obj)

    set_lod_model_properties(models, drawable_xml)

    if not has_skinned_models(drawable_xml):
        return models

    skinned_models = create_split_skinned_model(
        drawable_xml, materials, drawable_obj)

    set_skinned_model_properties(drawable_obj, drawable_xml)

    return skinned_models + models


def get_skinned_models_by_lod(drawable_xml: Drawable):
    """Get the skinned ``DrawableModels`` in ``drawable_xml`` mapped by LODLevel."""
    models_by_lod: dict[LODLevel, DrawableModel] = defaultdict(dict)

    for lod_level, models in zip(LODLevel, drawable_xml.model_groups):
        for model_xml in models:
            if model_xml.has_skin == 0:
                continue

            models_by_lod[lod_level] = model_xml

    return models_by_lod


def get_models_by_lod(drawable_xml: Drawable):
    """Get all non-skinned ``DrawableModels`` in ``drawable_xml`` mapped by LODLevel."""
    models_by_lod: dict[int, dict[LODLevel, DrawableModel]] = defaultdict(dict)

    for lod_level, models in zip(LODLevel, drawable_xml.model_groups):
        for i, model_xml in enumerate(models):
            models_by_lod[i][lod_level] = model_xml

    return list(models_by_lod.values())


def create_skinned_model(drawable_xml: Drawable, materials: list[bpy.types.Material], drawable_obj: bpy.types.Object):
    """Create the skinned portion of the mesh (parts of mesh with vertex groups)."""
    skinned_geometry_data = get_skinned_geom_data_by_lod(drawable_xml)

    name = f"{drawable_obj.name}.mesh_object"
    bones: list[Bone] = drawable_xml.skeleton.bones

    geom = create_drawable_geometry(
        name, skinned_geometry_data, materials, bones)
    add_armature_modifier(geom, drawable_obj)

    return geom


def create_non_skinned_models(drawable_xml: Drawable, materials: list[bpy.types.Material], drawable_obj: bpy.types.Object):
    """Create the non skinned models of the fragment mesh."""
    model_objs: list[bpy.types.Object] = []
    geometry_data = get_geom_data_by_bone(drawable_xml)

    bones: list[Bone] = drawable_xml.skeleton.bones

    if not bones:
        for bone_index, data_by_lod in geometry_data.items():
            name = f"{drawable_obj.name}.mesh_object"
            geom = create_drawable_geometry(
                name, data_by_lod, materials, bones)
            model_objs.append(geom)

        return model_objs

    for bone_index, data_by_lod in geometry_data.items():
        bone_name = bones[bone_index].name
        geom = create_drawable_geometry(
            bone_name, data_by_lod, materials, bones)
        add_armature_constraint(geom, drawable_obj, bone_name)

        model_objs.append(geom)

    return model_objs


def create_split_skinned_model(drawable_xml: Drawable, materials: list[bpy.types.Material], drawable_obj: bpy.types.Object):
    """Create skinned portion of mesh split by vertex groups."""
    grouped_geometry_data = group_drawable_geometries(drawable_xml)

    bones: list[Bone] = drawable_xml.skeleton.bones

    geoms: list[bpy.types.Object] = []

    for name, data_by_lod in grouped_geometry_data.items():
        geom = create_drawable_geometry(name, data_by_lod, materials, bones)
        add_armature_modifier(geom, drawable_obj)

        geoms.append(geom)

    return geoms


def create_drawable_geometry(name: str, geometry_data_by_lod: dict[LODLevel, GeometryData], materials: list[bpy.types.Material], bones: list[Bone]):
    """Create a single drawable geometry object. Requires a mapping of GeometryData to LODLevel."""
    geom: bpy.types.Object = create_mesh_object(SollumType.FRAG_GEOM, name)
    lod_levels: LODLevels = geom.sollumz_lods
    original_mesh = geom.data

    lod_levels.add_empty_lods()

    for lod_level, geometry_data in geometry_data_by_lod.items():
        mesh_name = f"{name}_{SOLLUMZ_UI_NAMES[lod_level].lower().replace(' ', '_')}"

        mesh_builder = MeshBuilder(geometry_data)
        lod_mesh = mesh_builder.build(mesh_name, materials)

        lod_levels.set_lod_mesh(lod_level, lod_mesh)
        lod_levels.set_active_lod(lod_level)

        mesh_builder.create_vertex_groups(geom, bones)

    lod_levels.set_active_lod(LODLevel.HIGH)

    if geom.data != original_mesh:
        bpy.data.meshes.remove(original_mesh)

    return geom


def set_skinned_model_properties(drawable_obj: bpy.types.Object, drawable_xml: Drawable):
    """Set drawable model properties for the skinned ``DrawableModel`` (only ever 1 skinned model per ``Drawable``)."""
    models_by_lods = get_skinned_models_by_lod(drawable_xml)

    if not models_by_lods:
        return

    # Use the first skinned model as there should only ever be 1 skinned drawable model
    for lod_level, model_xml in models_by_lods.items():
        skinned_model_props = drawable_obj.skinned_model_properties.get_lod(
            lod_level)

        set_drawable_model_properties(skinned_model_props, model_xml)


def set_lod_model_properties(model_objs: list[bpy.types.Object], drawable_xml: Drawable):
    """Set drawable model properties for each LOD mesh in ``model_objs``."""
    models_by_lods = get_models_by_lod(drawable_xml)

    for i, obj in enumerate(model_objs):
        model_xml = models_by_lods[i]
        obj_lods: LODLevels = obj.sollumz_lods

        for lod in obj_lods.lods:
            if lod.mesh is None:
                continue

            set_drawable_model_properties(
                lod.mesh.drawable_model_properties, model_xml[lod.type])


def set_drawable_model_properties(model_props: DrawableModelProperties, model_xml: DrawableModel):
    model_props.render_mask = model_xml.render_mask
    model_props.unknown_1 = model_xml.unknown_1
    model_props.flags = model_xml.flags


def get_skinned_geom_data_by_lod(drawable_xml: Drawable):
    """Creates mapping of ``GeometryData`` to ``LODLevel`` for the skinned model in ``drawable_xml``."""
    geom_data_by_lod: dict[LODLevel, GeometryData] = {}

    for lod_level, model_xml in get_skinned_models_by_lod(drawable_xml).items():
        geom_data = create_geometry_data_from_geometries(model_xml.geometries)
        geom_data_by_lod[lod_level] = geom_data

    return geom_data_by_lod


def get_geom_data_by_bone(drawable_xml: Drawable):
    """Maps the bone index of all non-skinned drawable models to a mapping of lod levels."""
    geom_data_by_bone: dict[int,
                            dict[LODLevel, GeometryData]] = defaultdict(dict)

    for lod_level, models in zip(LODLevel, drawable_xml.model_groups):
        for model_xml in models:
            if model_xml.has_skin == 1:
                continue

            geom_data = create_geometry_data_from_geometries(
                model_xml.geometries)
            geom_data_by_bone[model_xml.bone_index][lod_level] = geom_data

    return geom_data_by_bone


def create_geometry_data_from_geometries(geometries: list[Geometry]):
    """Creates a single GeometryData object for all geometries."""
    verts_by_shader, ind_by_shader = get_vertex_data_by_shader_index(
        geometries)
    geom_data = GeometryData()

    for shader_index, indices in ind_by_shader.items():
        num_verts = len(geom_data.vertices)

        geom_data.add_faces([i + num_verts for i in indices], shader_index)
        geom_data.add_vertices(verts_by_shader[shader_index])

    return geom_data


def group_drawable_geometries(drawable_xml: Drawable):
    """Splits drawable geometries by vertex group. Returns a map of group name to LODLevel to GeometryData object(s)."""
    lods_by_group: LODsByGroup = defaultdict(dict)
    models_by_lod = get_skinned_models_by_lod(drawable_xml)
    bones: list[Bone] = drawable_xml.skeleton.bones

    if not bones:
        return {}

    # In rigged drawables, there will be vertex groups that share the same vertices.
    # This will find the groups that share vertices and map them to a common parent.
    group_parent_map = get_group_parent_map(
        models_by_lod[LODLevel.HIGH], drawable_xml.skeleton.bones)

    for lod_level, model_xml in models_by_lod.items():
        geometry_data_by_group = create_grouped_geometry_data(
            model_xml, bones, group_parent_map)

        for group_name, geometry_data in geometry_data_by_group.items():
            lods_by_group[group_name][lod_level] = geometry_data

    return lods_by_group


def get_group_parent_map(model_xml: DrawableModel, bones: list[Bone]):
    """Get the vertex group indices that share the same vertices. Returns a map of bone index to bone parent index."""
    vertices, indices = get_joined_vertex_data(model_xml)
    faces = split_indices(indices)

    group_by_face: dict[GeometryData.Face, int] = {}
    parent_map: dict[int, int] = {}

    # Map groups to all groups sharing faces
    group_relations: dict[int, list[int]] = defaultdict(list)

    for face in faces:
        for i in face:
            vert_attrs = vertices[i]

            for bone_index in vert_attrs.weights:
                if face not in group_by_face:
                    group_by_face[face] = bone_index
                    continue

                if group_by_face[face] == bone_index:
                    continue

                if group_by_face[face] in group_relations[bone_index]:
                    continue

                group_relations[bone_index].append(group_by_face[face])

    # Find common parents of all related bones
    for bone_index, related_bones in group_relations.items():
        parent_map[bone_index] = find_common_root_bone_parent(
            related_bones, bones)

    return parent_map


def get_joined_vertex_data(model_xml: DrawableModel):
    """Returns list of vertices and indices of all the geometries. (vertices, indices)"""
    vertices: list[VertexAttributes] = []
    indices: list[int] = []

    for geometry in model_xml.geometries:
        for vert_index in geometry.index_buffer.data:
            indices.append(vert_index + len(vertices))

        for vert in geometry.vertex_buffer.get_data():
            vertices.append(VertexAttributes.from_vertex(vert))

    return vertices, indices


def find_common_root_bone_parent(bone_indices: list[int], bones: list[Bone]):
    """Find common bone parent of specified bones that is a child of the root bone. If none is found, the root bone is returned."""
    last_root_parent_index = -1

    for bone_index in bone_indices:
        root_parent_index = get_root_bone_parent_index(bone_index, bones)

        if last_root_parent_index == -1:
            last_root_parent_index = root_parent_index
            continue

        if last_root_parent_index != root_parent_index:
            return 0

    return root_parent_index


def get_root_bone_parent_index(bone_index: int, bones: list[Bone]) -> int:
    """Get parent of bone that is directly under the root bone."""
    i = bone_index

    while bones[i].parent_index > 0:
        i = bones[i].parent_index

    return i


def create_grouped_geometry_data(model_xml: DrawableModel, bones: list[Bone], group_parent_map: dict[int, int]):
    """Maps geometry data from a list of geometries by vertex group."""
    verts_by_shader, ind_by_shader = get_vertex_data_by_shader_index(
        model_xml.geometries)
    geom_data_by_group: dict[str, GeometryData] = defaultdict(GeometryData)

    for shader_index, indices in ind_by_shader.items():
        verts_by_group, ind_by_group = get_vertex_data_by_group(
            indices, verts_by_shader[shader_index], bones, group_parent_map)

        for group_name, verts in verts_by_group.items():
            geom_data = geom_data_by_group[group_name]
            num_verts = len(geom_data.vertices)
            group_indices = ind_by_group[group_name]

            geom_data.add_faces(
                [i + num_verts for i in group_indices], shader_index)
            geom_data.add_vertices(verts)

    return geom_data_by_group


def get_vertex_data_by_shader_index(geometries: list[Geometry]):
    """Returns the vertices and indices of the given geometries mapped by shader index."""
    verts_by_shader: dict[int, list[tuple]] = defaultdict(list)
    ind_by_shader: dict[int, list[int]] = defaultdict(list)

    for i, geometry in enumerate(geometries):
        vertex_data = geometry.vertex_buffer.get_data()
        index_data = geometry.index_buffer.data
        shader_index = geometry.shader_index

        if i > 0:
            index_data = [
                vert_index + len(verts_by_shader[shader_index]) for vert_index in index_data]

        # TODO: Remove once xml code is merged with GeometryData code
        verts_by_shader[shader_index].extend(
            [VertexAttributes.from_vertex(vert) for vert in vertex_data])
        ind_by_shader[shader_index].extend(index_data)

    return verts_by_shader, ind_by_shader


def get_vertex_data_by_group(indices: list[int], vertices: list[VertexAttributes], bones: list[Bone], group_parent_map: dict[int, int] = None):
    """Returns the given indices and vertices mapped by vertex group."""
    ind_by_group: dict[str, list[int]] = defaultdict(list)
    verts_by_group: dict[str, list[VertexAttributes]] = defaultdict(list)

    # Maps old vertex indices to new vertex indices
    vert_indices: dict[str, dict[int, int]] = defaultdict(dict)

    group_parent_map = group_parent_map or {}

    for i in indices:
        vertex = vertices[i]

        group_name = get_object_group_name(
            vertex, bones, group_parent_map)

        if i not in vert_indices[group_name]:
            group_verts = verts_by_group[group_name]

            group_verts.append(vertex)
            new_vert_index = len(group_verts) - 1

            vert_indices[group_name][i] = new_vert_index

        ind_by_group[group_name].append(vert_indices[group_name][i])

    return verts_by_group, ind_by_group


def get_object_group_name(vert_attrs: VertexAttributes, bones: list[Bone], group_parent_map: dict[int, int]):
    """Get the name of the object group that the vertex belongs to. This will just be the vertex group name unless
    the vertex is used in multiple vertex groups in which case the parent vertex group is used."""
    if group_parent_map:
        for bone_index in vert_attrs.weights:
            if bone_index not in group_parent_map:
                continue

            parent_index = group_parent_map[bone_index]

            return bones[parent_index].name

    first_bone_index = list(vert_attrs.weights.keys())[0]

    return bones[first_bone_index].name


def add_armature_modifier(obj: bpy.types.Object, drawable_obj: bpy.types.Object):
    mod: bpy.types.ArmatureModifier = obj.modifiers.new("skel", "ARMATURE")
    mod.object = drawable_obj

    return mod


def add_armature_constraint(obj: bpy.types.Object, drawable_obj: bpy.types.Object, target_bone: str, set_transforms=True):
    """Add armature constraint that is used for bone parenting on non-skinned objects."""
    constraint: bpy.types.ArmatureConstraint = obj.constraints.new("ARMATURE")
    target = constraint.targets.new()
    target.target = drawable_obj
    target.subtarget = target_bone

    if not set_transforms:
        return

    bone = drawable_obj.data.bones[target_bone]
    obj.matrix_local = bone.matrix_local


def has_skinned_models(drawable_xml: Drawable):
    for model_xml in drawable_xml.all_models:
        if model_xml.has_skin == 1:
            return True