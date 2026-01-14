# -*- coding: utf-8 -*-
"""
Created on Wed Feb 2 13:12:15 2022

@author: kunal
"""
from align.schema.types import set_context
import logging
import os
import pathlib


from align.schema import SubCircuit, constraint, Library, Instance
from .util import gen_key, get_generator, get_base_model

logger = logging.getLogger(__name__)


class PrimitiveLibrary():

    def __init__(self, ckt_lib: Library, pdk_dir: pathlib.Path):
        pdk_models = get_generator('pdk_models', pdk_dir)
        self.pdk_dir = pdk_dir
        self.plib = Library(loadbuiltins=False, pdk_models=pdk_models)
        self.ckt_lib = ckt_lib

    @staticmethod
    def _generator_name_from_base(base_model):
        if not base_model:
            return None
        base = base_model.upper()
        if 'MOS' in base:
            return 'MOS'
        if 'CAP' in base:
            return 'CAP'
        if 'RES' in base:
            return 'RES'
        return None

    def gen_primitive_collateral(self):
        """
        create a unique name for each instance and
        Args:
            ckt_data ([type]): ckt library after annotation
        Returns:
            primitives: library of primitives
        """

        for ckt in self.ckt_lib:
            if not isinstance(ckt, SubCircuit):
                continue
            elif [True for const in ckt.constraints if isinstance(const, constraint.Generator)]:
                continue
            logger.debug(f"Found module: {ckt.name} {ckt.elements} {ckt.pins}")
            group_cap_instances = []
            for const in ckt.constraints:
                if isinstance(const, constraint.GroupCaps):
                    group_cap_instances.append(const.name.upper())
                    self.group_cap_subcircuit(const.unit_cap.upper())
            logger.debug(f"found group cap instances {group_cap_instances}")

            for ele in ckt.elements:
                if ele.name in group_cap_instances:
                    ele.add_abs_name(ele.model)
                    logger.debug(f"group cap instance {ele}")
                else:
                    self.gen_primitive_def(ele)
        return self.plib

    def group_cap_subcircuit(self, unit_cap):
        # TODO hack for group cap, need to be fixed
        """create subckt corresponding to unit cap
        Args:
            name (str): unique cap name in constraint
        """
        if not self.plib.find(unit_cap):
            logger.debug(f"creating subcircuit for {unit_cap}")
            cmodel = self.ckt_lib.find('CAP')
            assert cmodel, f"no cap model found for groupcap constraint {cmodel}"
            self.add_primitve('CAP')
            unit_cap_value = float(unit_cap.split('_')[1].replace('F', ''))*10E-15

            with set_context(self.plib):
                new_subckt = SubCircuit(name=unit_cap, pins=list(cmodel.pins), generator={"name": 'CAP'})
            with set_context(new_subckt.elements):
                new_ele = Instance(name='C0', model='CAP',
                                   pins={pin: pin for pin in cmodel.pins},
                                   parameters={'VALUE': unit_cap_value}
                                   )
                new_subckt.elements.append(new_ele)
            self.plib.append(new_subckt)

    def create_subckt(self, element, name, generator_name=None):
        """create_subckt
        Adds a subckt in primitive library for a generic device instance if not already existing
        Args:
            element (instance): instance in a subcircuit
            name (str): unique name for this instance based on parameters
        """
        if not self.plib.find(name):
            logger.debug(f"creating subcircuit for {element}")
            with set_context(self.plib):
                new_subckt = SubCircuit(
                    name=name,
                    pins=list(element.pins.keys()),
                    generator={"name": generator_name or element.model}
                )
            with set_context(new_subckt.elements):
                new_ele = Instance(name=element.name,
                                   model=element.model,
                                   pins={x: x for x in element.pins.keys()},
                                   parameters=element.parameters
                                   )
                new_subckt.elements.append(new_ele)
            self.plib.append(new_subckt)

    def gen_primitive_def(self, element):
        """gen_primitive_def

            Adds subcircuits to primitive library for each instance with a different parameter

        Args:
            element (instance): instance properties
        """
        model = element.model
        generator = self.ckt_lib.find(model)

        if isinstance(generator, SubCircuit):
            element.add_abs_name(model)
            gen_const = [True for const in generator.constraints if isinstance(const, constraint.Generator)]
            if gen_const and not self.plib.find(generator.name):
                self.add_primitve(generator.name)
        elif get_generator(element.model, self.pdk_dir):
            block_arg = gen_key(element.parameters)
            unique_name = f'{model}{block_arg}'
            element.add_abs_name(unique_name)
            self.add_primitve(model)
            base_model = get_base_model(element.parent.parent, element.name)
            use_blackbox = False
            if base_model and base_model.upper().startswith("RES") and self.pdk_dir.name.upper() == "SKY130_PDK":
                if os.getenv("ALIGN_SKY130_RES_BLACKBOX", "").lower() in {"1", "true", "yes", "on"}:
                    use_blackbox = True
                elif os.getenv("ALIGN_SKY130_ENABLE_RES", "").lower() not in {"1", "true", "yes", "on"}:
                    raise NotImplementedError(
                        "SKY130 resistor generator is not DRC-clean yet; set ALIGN_SKY130_ENABLE_RES=1 to force "
                        "or ALIGN_SKY130_RES_BLACKBOX=1 with -b <dir> to use resistor macros."
                    )
            generator_name = self._generator_name_from_base(base_model) or model
            self.create_subckt(element, unique_name, generator_name=generator_name)
            if use_blackbox:
                primitive_subckt = self.plib.find(unique_name)
                if primitive_subckt:
                    gen_constraints = [
                        const for const in primitive_subckt.constraints
                        if isinstance(const, constraint.Generator)
                    ]
                    if not any(const.name == "black_box" for const in gen_constraints):
                        with set_context(primitive_subckt.constraints):
                            primitive_subckt.constraints.append(constraint.Generator(name="black_box"))
        else:
            base_model = get_base_model(element.parent.parent, element.name)
            msg = (
                f"Unmatched generator for instance {element.name} (model={element.model}, base={base_model}) "
                f"in {element.parent.parent.name}; no generator found in PDK {self.pdk_dir}."
            )
            if base_model and base_model.upper().startswith("IND"):
                raise NotImplementedError(
                    f"{msg} Inductors are unsupported; use a black_box or remove the inductor."
                )
            if base_model and base_model.upper().startswith("DIODE"):
                raise NotImplementedError(
                    f"{msg} Diodes are unsupported; add a black_box or replace the diode."
                )
            raise NotImplementedError(msg)

    def add_primitve(self, primitive_name):
        if not self.plib.find(primitive_name):
            primitive = self.ckt_lib.find(primitive_name)
            # add model for instances in the primitive first
            if isinstance(primitive, SubCircuit):
                with set_context(self.plib):
                    for e in primitive.elements:
                        if not self.plib.find(e.model):
                            self.plib.append(self.ckt_lib.find(e.model))
            with set_context(self.plib):
                self.plib.append(primitive)
