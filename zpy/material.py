"""
    Utilities for Materials in Blender.
"""
import logging
import random
from pathlib import Path
from typing import Tuple, Union

import bpy
import gin
import numpy as np

import zpy

log = logging.getLogger(__name__)


def verify(mat: Union[str, bpy.types.Material], check_none=True) -> bpy.types.Material:
    """ Return material given name or Material type object. """
    if isinstance(mat, str):
        mat = bpy.data.materials.get(mat)
    if check_none and mat is None:
        raise ValueError(f'Could not find material {mat}.')
    return mat


_SAVED_MATERIALS = {}


def save_material_props(mat: Union[str, bpy.types.Material]) -> None:
    """ Save a pose (rot and pos) to dict. """
    log.info(f'Saving material properties for {mat.name}')
    _SAVED_MATERIALS[mat.name] = get_mat_props(mat)


def restore_material_props(mat: Union[str, bpy.types.Material]) -> None:
    """ Restore an object to a position. """
    log.info(f'Restoring material properties for {mat.name}')
    set_mat_props(mat, _SAVED_MATERIALS[mat.name])


def restore_all_material_props() -> None:
    """ Restore all jittered materials to original look. """
    for mat_name, mat_props in _SAVED_MATERIALS.items():
        set_mat_props(mat_name, mat_props)


def get_mat_props(
    mat: Union[str, bpy.types.Material],
) -> Tuple[float]:
    """ Get (some of the) material properties. """
    mat = verify(mat)
    bsdf_node = mat.node_tree.nodes.get('Principled BSDF')
    if bsdf_node is None:
        log.warning(f'No BSDF node in {mat.name}')
        return (0.0, 0.0, 0.0)
    return (
        bsdf_node.inputs['Roughness'].default_value,
        bsdf_node.inputs['Metallic'].default_value,
        bsdf_node.inputs['Specular'].default_value,
    )


def set_mat_props(
    mat: Union[str, bpy.types.Material],
    prop_tuple: Tuple[float]
) -> None:
    """ Set (some of the) material properties. """
    mat = verify(mat)
    # TODO: Work backwards from Material output node instead of
    #       assuming a 'Principled BSDF' node
    bsdf_node = mat.node_tree.nodes.get('Principled BSDF', None)
    if bsdf_node is None:
        log.warning(f'No BSDF node in {mat.name}')
        return
    bsdf_node.inputs['Roughness'].default_value += prop_tuple[0]
    bsdf_node.inputs['Metallic'].default_value += prop_tuple[1]
    bsdf_node.inputs['Specular'].default_value += prop_tuple[2]


@gin.configurable
def jitter(
    mat: Union[str, bpy.types.Material],
    std: float = 0.2,
    save_first_time: bool = True,
) -> None:
    """ Randomize an existing material a little. """
    mat = verify(mat)
    # Save the material props first time jitter is called
    # and restore before jittering every subsequent time
    if save_first_time:
        if _SAVED_MATERIALS.get(mat.name, None) is None:
            save_material_props(mat)
        else:
            restore_material_props(mat)
    log.info(f'Jittering material {mat.name}')
    mat_props = get_mat_props(mat)
    jittered_mat_props = tuple(
        map(lambda p: p + random.gauss(0, std), mat_props))
    set_mat_props(mat, jittered_mat_props)


@gin.configurable
def make_mat_from_texture(
    texture_path: Union[str, Path],
    name: str = None,
) -> bpy.types.Material:
    """ Makes a material from a texture or color."""
    texture_path = zpy.files.verify_path(texture_path, make=False)
    if name is None:
        name = texture_path.stem
    mat = bpy.data.materials.get(name, None)
    if mat is None:
        log.debug(f'Material {name} does not exist, creating it.')
        mat = bpy.data.materials.new(name=name)
    mat.use_nodes = True
    bsdf_node = mat.node_tree.nodes.get('Principled BSDF')
    out_node = mat.node_tree.nodes.get('Material Output')
    tex_node = mat.node_tree.nodes.new('ShaderNodeTexImage')
    tex_node.name = 'ImageTexture'
    coord_node = mat.node_tree.nodes.new('ShaderNodeTexCoord')
    bpy.ops.image.open(filepath=str(texture_path))
    tex_node.image = bpy.data.images[texture_path.name]
    tex_node.image.colorspace_settings.name = 'Filmic Log'
    mat.node_tree.links.new(tex_node.outputs[0], bsdf_node.inputs[0])
    mat.node_tree.links.new(coord_node.outputs[0], tex_node.inputs[0])
    tex_node.image.reload()
    return mat

@gin.configurable
def make_mat_from_color(
    color: Tuple[float],
    name: str = None,
) -> bpy.types.Material:
    """ Makes a material from a texture or color."""
    if name is None:
        name = str(color)
    mat = bpy.data.materials.get(name, None)
    if mat is None:
        log.debug(f'Material {name} does not exist, creating it.')
        mat = bpy.data.materials.new(name=name)
    mat.use_nodes = True
    bsdf_node = mat.node_tree.nodes.get('Principled BSDF')
    out_node = mat.node_tree.nodes.get('Material Output')
    mat.node_tree.nodes.remove(bsdf_node)
    bsdf_node = mat.node_tree.nodes.new('ShaderNodeBsdfDiffuse')
    bsdf_node.inputs['Color'].default_value = color + (1.,)
    mat.node_tree.links.new(out_node.inputs[0], bsdf_node.outputs[0])
    return mat


def set_mat(
    obj: Union[str, bpy.types.Object],
    mat: Union[str, bpy.types.Material],
    recursive: bool = True,
) -> None:
    """ Recursively sets object material.

    Allows string material and object names as input.
    """
    obj = zpy.objects.verify(obj)
    mat = zpy.material.verify(mat)
    if hasattr(obj, 'active_material'):
        log.debug(f'Setting object {obj.name} material {mat.name}')
        obj.active_material = mat
    else:
        log.warning('Object does not have material property')
        return
    # Recursively change material on all children of object
    if recursive:
        for child in obj.children:
            set_mat(child, mat)


@gin.configurable
def make_aov_material_output_node(
    mat: bpy.types.Material = None,
    obj: bpy.types.Object = None,
    style: str = 'instance',
) -> None:
    """ Make AOV Output nodes in Composition Graph. """
    # Make sure engine is set to Cycles
    if not (bpy.context.scene.render.engine == "CYCLES"):
        log.warning(' Setting render engine to CYCLES to use AOV')
        bpy.context.scene.render.engine == "CYCLES"
    # Only certain styles are available
    valid_styles = ['instance', 'category']
    assert style in valid_styles, \
        f'Invalid style {style} for AOV material output node, must be in {valid_styles}.'

    # HACK: multiple material slots
    all_mats = []

    # Use material
    if mat is not None:
        all_mats = [mat]
    # Get material from object
    elif obj is not None:
        if obj.active_material is None:
            log.debug(f'No active material found for {obj.name}')
            return
        if len(obj.material_slots) > 1:
            for mat in obj.material_slots:
                all_mats.append(mat.material)
        else:
            all_mats.append(obj.active_material)
    else:
        raise ValueError('Must pass in an Object or Material')

    # HACK: multiple material slots
    for mat in all_mats:

        # Make sure material is using nodes
        if not mat.use_nodes:
            mat.use_nodes = True
        _tree = mat.node_tree

        # Vertex Color Node
        _name = f'{style} Vertex Color'
        vertexcolor_node = _tree.nodes.get(_name)
        if vertexcolor_node is None:
            vertexcolor_node = _tree.nodes.new('ShaderNodeVertexColor')
        vertexcolor_node.layer_name = style
        vertexcolor_node.name = _name

        # AOV Output Node
        _name = style
        # HACK: property "name" of ShaderNodeOutputAOV behaves strangely with .get()
        aovoutput_node = None
        for _node in _tree.nodes:
            if _node.name == _name:
                aovoutput_node = _node
        if aovoutput_node is None:
            aovoutput_node = _tree.nodes.new('ShaderNodeOutputAOV')
        aovoutput_node.name = style
        _tree.links.new(vertexcolor_node.outputs['Color'],
                        aovoutput_node.inputs['Color'])
