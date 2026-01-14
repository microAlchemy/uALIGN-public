import os
from types import SimpleNamespace

from ..cell_fabric import transformation, pdk
from ..cell_fabric.generators import Region
from ..cell_fabric.grid import UncoloredCenterLineGrid
from ..compiler.util import get_generator
import itertools
import json
import pathlib
import re
from .router import NType

from .render_placement import gen_transformation

import logging
logger = logging.getLogger(__name__)

def rational_scaling( d, *, mul=1, div=1, errors=None):
    assert all( (mul*c) % div == 0 for c in d['bbox'])
    d['bbox'] = [ (mul*c) //div for c in d['bbox']]
    for term in d['terminals']:
        if not all( (mul*c) % div == 0 for c in term['rect']):
            txt = f"Terminal {term} not a multiple of {div} (mul={mul})."
            if errors is not None:
                errors.append( txt)
            logger.error( txt)

        term['rect'] = [ (mul*c)//div for c in term['rect']]


def gen_viewer_json(hN, *, pdkdir, draw_grid=False, global_route_json=None, json_dir=None, extract=False, input_dir=None, markers=False,
                    toplevel=True, pnr_const_ds=None):

    logger.debug(f'Checking: {hN.name}')

    global_power_names = set( [ n.name for n in hN.PowerNets])

    generator = get_generator('MOSGenerator', pdkdir)
    # TODO: Remove these hardcoded widths & heights from __init__()
    #       (Height may be okay since it defines UnitCellHeight)
    cnv = generator(pdk.Pdk().load(pdkdir / 'layers.json'),28,12,2,3,1,1,1)

    with open(cnv.pdk.layerfile, "rt") as fp:
        scale_factor = json.load(fp)["ScaleFactor"]
    # PnRDB coordinates are in units of 2nm. All else is in PDK abstraction.
    assert scale_factor == 1 or scale_factor % 2 == 0, f'PDK ScaleFactor should be even.'

    pdk_name = str(pdkdir).lower()
    pdk_is_sky130 = "sky130" in pdk_name
    strict_sky130 = os.getenv("ALIGN_SKY130_STRICT", "").lower() in {"1", "true", "yes", "on"}
    relax_m4_blockpins = pdk_is_sky130 and not strict_sky130
    relax_m4_paths = False
    if pdk_is_sky130:
        if strict_sky130:
            env_relax_paths = os.getenv("ALIGN_SKY130_STRICT_RELAX_M4_PATHS")
            if env_relax_paths is None or env_relax_paths == "":
                relax_m4_paths = True
            else:
                relax_m4_paths = env_relax_paths.lower() in {"1", "true", "yes", "on"}
        else:
            relax_m4_paths = True
    if "sky130" in pdk_name:
        for layer in ("CapMIMContact", "CapMIMLayer"):
            if not any(getattr(gen, "layer", None) == layer for gen in cnv.generators.values()):
                cnv.addGen(Region(f"sky130_{layer.lower()}", layer, h_grid=cnv.m2.clg, v_grid=cnv.m1.clg))

    strict_m4_gen = None
    if strict_sky130 and "sky130" in pdk_name:
        env_pitch = os.getenv("ALIGN_SKY130_STRICT_M4_PITCH")
        env_offset = os.getenv("ALIGN_SKY130_STRICT_M4_OFFSET")
        default_pitch = cnv.pdk["M4"]["Pitch"]
        default_offset = cnv.pdk["M4"].get("Offset", 0)
        if env_pitch is not None:
            try:
                m4_pitch = int(env_pitch)
            except ValueError:
                m4_pitch = default_pitch
        else:
            m4_pitch = default_pitch
        if env_offset is not None:
            try:
                m4_offset = int(env_offset)
            except ValueError:
                m4_offset = default_offset
        else:
            m4_offset = default_offset
        m4_clg = UncoloredCenterLineGrid(
            pitch=m4_pitch,
            width=cnv.pdk["M4"]["Width"],
            offset=m4_offset,
        )
        strict_m4_gen = SimpleNamespace(clg=m4_clg)

    terminals = []

    subinsts = {}

    errors = []

    def add_terminal( netName, layer, b, tag=None, center_override=None):

        r = [ b.LL.x, b.LL.y, b.UR.x, b.UR.y]
        terminals.append( { "netName": netName, "netType": "drawing", "layer": layer, "rect": r})

        def f( gens, value, tag=None):
            # value is in units of 0.5nm
            if value%2 != 0:
                txt = f"Off grid:{tag} {layer} {netName} {r} {r[2]-r[0]} {r[3]-r[1]}: {value} (in 2x units) is not divisible by two."
                errors.append( txt)
                logger.error( txt)
            else:
                value = value * scale_factor // 2
                bounds = [gen.clg.inverseBounds(value) for gen in gens]
                if not any(p[0] == p[1] for p in bounds):
                    if len(bounds) == 1:
                        detail = bounds[0]
                    else:
                        detail = bounds
                    txt = f"Off grid:{tag} {layer} {netName} {r} {r[2]-r[0]} {r[3]-r[1]}: {value} doesn't land on grid, lb and ub are: {detail}"
                    errors.append( txt)
                    logger.error( txt)

        if layer == "cellarea":
            f( [cnv.m1], b.LL.x, "LL.x")
            f( [cnv.m1], b.UR.x, "UR.x")
            f( [cnv.m2], b.LL.y, "LL.y")
            f( [cnv.m2], b.UR.y, "UR.y")
        else:
            if center_override is not None:
                center = center_override
            elif layer in ["M1", "M3", "M5"]:
                center = (b.LL.x + b.UR.x)//2
            elif layer in ["M2", "M4", "M6"]:
                center = (b.LL.y + b.UR.y)//2
            else:
                center = None
            if center is not None:
                if relax_m4_blockpins and layer == "M4" and tag == "blockPin":
                    return
                lyr = layer.lower() if layer.lower() in cnv.generators else layer.upper()
                if layer == "M4" and strict_m4_gen is not None:
                    gens = [strict_m4_gen]
                else:
                    gens = [cnv.generators[lyr]]
                if relax_m4_paths and layer == "M4" and tag in {"path_metal", "path_via", "intervia"}:
                    m2_gen = cnv.generators.get("m2")
                    if m2_gen is not None and m2_gen not in gens:
                        gens.append(m2_gen)
                f(gens, center, tag)

    fa_map = {}
    for n in itertools.chain( hN.Nets, hN.PowerNets):
        for c in n.connected:
            if c.type == 'Block' or c.type == NType.Block:
                cblk = hN.Blocks[c.iter2]
                blk = cblk.instance[cblk.selectedInstance]
                block_name = blk.name
                master_name = blk.master
                pin = blk.blockPins[c.iter]
                formal_name = f"{blk.name}/{pin.name}"
                assert formal_name not in fa_map
                fa_map[formal_name] = n.name
            elif c.type == 'Terminal' or c.type == NType.Terminal:
                term = hN.Terminals[c.iter]
                terminal_name = term.name
                assert n.name == terminal_name
            else:
                assert False, c.type

    for cblk in hN.Blocks:
        blk = cblk.instance[cblk.selectedInstance]
        found = False
        if json_dir is not None:
            pth = pathlib.Path( json_dir + "/" + blk.lefmaster + ".json")
            if not pth.is_file():
                logger.debug( f"{pth} is not available; not importing subblock rectangles")
            else:
                found = True

        if not found and input_dir is not None:

            logger.debug( f"blk.gdsFile: {blk.gdsFile} {found} {input_dir}")
            p = re.compile( r"^(\./|)Results/(\S+)\.gds$")
            m = p.match( blk.gdsFile)
            if m:
                pth = input_dir / (m.groups()[1] + ".json")
                if not pth.is_file():
                    logger.error( f"{pth} not found in input_dir")
                else:
                    logger.debug( f"{pth} found in input_dir")
                    found = True
            else:
                logger.error( f"'{blk.gdsFile}' does not match pattern {p.pattern}")

        if found:
            with pth.open( "rt") as fp:
                d = json.load( fp)
            # PnRDB coordinates are in units of 0.5nm. Scale primitives to this unit.
            rational_scaling( d, mul=2, div=scale_factor, errors=errors)

            tr3 = gen_transformation( blk)
            for term in d['terminals']:
                term['rect'] = tr3.hitRect( transformation.Rect( *term['rect'])).canonical().toList()

            for term in d['terminals']:
                nm = term['netName']
                if nm is not None:
                    formal_name = f"{blk.name}/{nm}"
                    default_name = nm if nm in global_power_names else formal_name
                    term['netName'] = fa_map.get( formal_name, default_name)
                if 'pin' in term:
                    del term['pin']
                if 'terminal' in term:
                    assert len(term['terminal']) == 2
                    term['netName'] = f"{blk.name}/{':'.join(term['terminal'])}"
                    term['terminal'] = [f"{blk.name}/{term['terminal'][0]}", term['terminal'][1]]
                if term['layer'] not in ["boundary"]:
                    terminals.append( term)

            if 'subinsts' in d:
                subinsts.update({f'{blk.name}/{nm}': v for nm, v in d['subinsts'].items()})

    def addt(net, obj, con, tag=None, center_override=None):
        b = con.placedBox
        if obj == net:
            add_terminal( net.name, con.metal, b, tag=tag, center_override=center_override)
        else:
            add_terminal( obj, con.metal, b, tag=tag, center_override=center_override)

    def placed_center_value(con, layer):
        if not hasattr(con, "placedCenter"):
            return None
        if layer in ["M1", "M3", "M5"]:
            return con.placedCenter.x
        if layer in ["M2", "M4", "M6"]:
            return con.placedCenter.y
        return None

    for n in itertools.chain( hN.Nets, hN.PowerNets):
        logger.debug( f"Net: {n.name}")

        for c in n.connected:
            if c.type == 'Block' or c.type == NType.Block:
                cblk = hN.Blocks[c.iter2]
                blk = cblk.instance[cblk.selectedInstance]
                block_name = blk.name
                master_name = blk.master
                pin = blk.blockPins[c.iter]
                formal_name = pin.name

                tag = f'Block formal_index: {c.iter},{formal_name} block_index: {c.iter2},{block_name},{master_name}'
                logger.debug( f'\t{tag}')

                for con in pin.pinContacts:
                    addt( n, n, con, "blockPin")
            elif c.type == 'Terminal' or c.type == NType.Terminal:
                term = hN.Terminals[c.iter]
                terminal_name = term.name
                assert terminal_name == n.name
                tag = f'Terminal formal_index: {c.iter},{terminal_name}'
                logger.debug( f'\t{tag}')
                for con in term.termContacts:
                    pass
                    # addt( n, con)
            else:
                assert False, c.type


        for metal in n.path_metal:
            con = metal.MetalRect
            center_override = placed_center_value(con, con.metal)
            add_terminal( n.name, con.metal, con.placedBox, "path_metal", center_override=center_override)

        for via in n.path_via:
            for con in [via.UpperMetalRect,via.LowerMetalRect,via.ViaRect]:
                center_override = placed_center_value(con, con.metal)
                addt( n, n, con, "path_via", center_override=center_override)

        if hasattr( n, 'interVias'):        
            for via in n.interVias:
                for con in [via.UpperMetalRect,via.LowerMetalRect,via.ViaRect]:
                    center_override = placed_center_value(con, con.metal)
                    addt( n, n, con, "intervia", center_override=center_override)

    for pg in [hN.Gnd, hN.Vdd]:
        for metal in pg.metals:
            con = metal.MetalRect
            add_terminal( pg.name, con.metal, con.placedBox, "power grid metal")
        for via in pg.vias:
            for con in [via.UpperMetalRect,via.LowerMetalRect,via.ViaRect]:
                add_terminal( pg.name, con.metal, con.placedBox, "power grid via")

    if global_route_json is not None:
        with open(global_route_json, "rt") as fp:
            gr_json = json.load( fp)
        tbl = {}
        for wire in gr_json['wires']:
            nm = wire['net_name']
            if nm not in tbl:
                tbl[nm] = []
            tbl[nm].append(wire)

        for (k,vv) in tbl.items():
            for v in vv:
                for conn in v['connected_pins']:
                    ly = conn['layer']
                    r = conn['rect'][:]
                    for q in [0,1]:
                        r[q], r[q+2] = min(r[q],r[q+2]), max(r[q],r[q+2])

                    if ly != "":
                        d0 = {"netName": k+"_tm", "netType": "drawing", "layer": ly, "rect": r}
                        d1 = {"netName": conn['sink_name'], "netType": "drawing", "layer": ly, "rect": r}
                        logger.debug( f"Add two terminals: {d0} {d1}")
                        terminals.append( d0)
                        terminals.append( d1)

                ly = v['layer']
                if 'rect' not in v:
                    logger.error( f"No global route 'rect' in {v}")
                    continue

                r = v['rect'][:]
                for q in [0,1]:
                    r[q], r[q+2] = min(r[q],r[q+2]), max(r[q],r[q+2])

                if  r[0] < r[2] and r[1] < r[3]:
                    logger.error( f"2-dimensional global route {v} {r}")
                if r[0] == r[2] and r[1] == r[3]:
                    logger.error( f"0-dimensional global route {v} {r}")

                logger.debug( f"Global route: {k} {ly} {r}")

                for q in [0,1]:
                    if r[q] == r[q+2]:
                        r[q]   -= 20
                        r[q+2] += 20

                terminals.append( {"netName": k+"_gr", "netType": "drawing", "layer": ly, "rect": r})

        if draw_grid:
            m1_pitch = 2*10*cnv.pdk['M1']['Pitch']
            m2_pitch = 2*10*cnv.pdk['M2']['Pitch']
            for ix in range( (hN.width+m1_pitch-1)//m1_pitch):
                x = m1_pitch*ix
                r = [ x-2, 0, x+2, hN.height]
                terminals.append( { "netName": 'm1_bin', "netType": "drawing", "layer": 'M1', "rect": r})

            for iy in range( (hN.height+m2_pitch-1)//m2_pitch):
                y = m2_pitch*iy
                r = [ 0, y-2, hN.width, y+2]
                terminals.append( { "netName": 'm2_bin', "netType": "drawing", "layer": 'M2', "rect": r})

    # Create viewer dictionary

    d = {}

    logger.debug( f'bbox: {hN.LL.x} {hN.LL.y} {hN.UR.x} {hN.UR.y}')

    d["bbox"] = [hN.LL.x,hN.LL.y,hN.UR.x,hN.UR.y]

    d["globalRoutes"] = []

    d["globalRouteGrid"] = []

    d["terminals"] = terminals

    # PnRDB coordinates are in units of 0.5nm. Scale back to PDK.
    rational_scaling(d, mul=scale_factor, div=2, errors=errors)

    cnv.bbox = transformation.Rect( *d["bbox"])
    cnv.terminals = d["terminals"]
    for inst, parameters in subinsts.items():
        cnv.subinsts[inst].parameters.update(parameters)

    nets_dnr = []
    if pnr_const_ds is not None and hN.name in pnr_const_ds:
        for const in pnr_const_ds[hN.name]['constraints']:
            if const['const_name'] == 'DoNotRoute':
                nets_dnr.extend(const['nets'])

    nets_allowed_to_be_open = set(nets_dnr)
    if not toplevel:
        nets_allowed_to_be_open = set.union(nets_allowed_to_be_open, global_power_names)

    new_d = cnv.gen_data(run_drc=True, run_pex=extract,nets_allowed_to_be_open=nets_allowed_to_be_open,postprocess=toplevel)

    d['bbox'] = cnv.bbox.toList()
    d['terminals'] = new_d['terminals']

    if False:
        nets_actual = set.union({net.name for net in hN.Nets}, {net.name for net in hN.PowerNets})
        nets_found  = {term['netName'] for term in d['terminals'] if term['netName'] is not None}
        logger.debug(f'Nets actual: {nets_actual}')
        logger.debug(f'Nets found: {nets_found}')
        for net in nets_found - nets_actual:
            txt = f"Bogus net in the generated layout: {net}"
            errors.append( txt)
            logger.error( txt)

    for e in errors:
        cnv.drc.errors.append( e)

    return (cnv, d)
