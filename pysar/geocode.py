#!/usr/bin/env python3
############################################################
# Program is part of PySAR                                 #
# Copyright(c) 2017-2018, Zhang Yunjun                     #
# Author:  Zhang Yunjun, 2017                              #
############################################################


import os
import sys
import time
import argparse
import warnings
import multiprocessing
import numpy as np
from pysar.objects.resample import resample
from pysar.utils import readfile, writefile, utils as ut


######################################################################################
TEMPLATE = """template:
pysar.geocode              = auto  #[yes / no], auto for yes
pysar.geocode.SNWE         = auto  #[-1.2,0.5,-92,-91 / no ], auto for no, output coverage in S N W E in degree 
pysar.geocode.latStep      = auto  #[0.0-90.0 / None], auto for None, output resolution in degree
pysar.geocode.lonStep      = auto  #[0.0-180.0 / None], auto for None - calculate from lookup file
pysar.geocode.interpMethod = auto  #[nearest], auto for nearest, interpolation method
pysar.geocode.fillValue    = auto  #[np.nan, 0, ...], auto for np.nan, fill value for outliers.
"""

EXAMPLE = """example:
  geocode.py velocity.h5
  geocode.py velocity.h5 -b -0.5 -0.25 -91.3 -91.1
  geocode.py velocity.h5 timeseries.h5 -t pysarApp_template.txt -o ./GEOCODE --update

  geocode.py geo_velocity.h5 --geo2radar
"""


def create_parser():
    parser = argparse.ArgumentParser(description='Resample radar coded files into geo coordinates, or reverse',
                                     formatter_class=argparse.RawTextHelpFormatter,
                                     epilog=TEMPLATE + '\n' + EXAMPLE)

    parser.add_argument('file', nargs='+', help='File(s) to be geocoded')
    parser.add_argument('-d', '--dset', help='dataset to be geocoded, for example:\n' +
                        'height                        for geometryRadar.h5\n' +
                        'unwrapPhase-20100114_20101017 for ifgramStack.h5')
    parser.add_argument('--geo2radar', '--reverse', dest='radar2geo', action='store_false',
                        help='reverse geocoding, or resample geocoded files into radar coordinates.\n' +
                        'For radar coded lookup table (ISCE, Doris) only.')

    parser.add_argument('-l', '--lookup', dest='lookupFile',
                        help='Lookup table file generated by InSAR processors.')
    parser.add_argument('-t', '--template', dest='templateFile',
                        help="Template file with geocoding options.")

    parser.add_argument('-b', '--bbox', dest='SNWE', type=float, nargs=4, metavar=('S', 'N', 'W', 'E'),
                        help='Bounding box of area to be geocoded.\n' +
                        'Include the uppler left corner of the first pixel' +
                        '    and the lower right corner of the last pixel')
    parser.add_argument('-y', '--lat-step', dest='latStep', type=float,
                        help='output pixel size in degree in latitude.')
    parser.add_argument('-x', '--lon-step', dest='lonStep', type=float,
                        help='output pixel size in degree in longitude.')

    parser.add_argument('-i', '--interpolate', dest='interpMethod', choices={'nearest', 'bilinear'},
                        help='interpolation/resampling method. Default: nearest', default='nearest')
    parser.add_argument('--fill', dest='fillValue', type=float, default=np.nan,
                        help='Value used for points outside of the interpolation domain.\n' +
                             'Default: np.nan')

    parser.add_argument('--update', dest='updateMode', action='store_true',
                        help='skip resampling if output file exists and newer than input file')
    parser.add_argument('-o', '--output', dest='outfile',
                        help="output file name. Default: add prefix 'geo_'")
    parser.add_argument('--outdir', '--output-dir', dest='out_dir', help='output directory.')

    return parser


def cmd_line_parse(iargs=None):
    parser = create_parser()
    inps = parser.parse_args(args=iargs)
    return inps


def _check_inps(inps):
    inps.file = ut.get_file_list(inps.file)
    if not inps.file:
        raise Exception('ERROR: no input file found!')
    elif len(inps.file) > 1:
        inps.outfile = None

    atr = readfile.read_attribute(inps.file[0])
    if 'Y_FIRST' in atr.keys() and inps.radar2geo:
        print('input file is already geocoded')
        print('to resample geocoded files into radar coordinates, use --geo2radar option')
        print('exit without doing anything.')
        sys.exit(0)
    elif 'Y_FIRST' not in atr.keys() and not inps.radar2geo:
        print('input file is already in radar coordinates, exit without doing anything')
        sys.exit(0)

    inps.lookupFile = ut.get_lookup_file(inps.lookupFile)
    if not inps.lookupFile:
        raise FileNotFoundError('No lookup table found! Can not geocode without it.')

    if inps.SNWE:
        inps.SNWE = tuple(inps.SNWE)

    inps.laloStep = [inps.latStep, inps.lonStep]
    if None in inps.laloStep:
        inps.laloStep = None
    return inps


def read_template2inps(template_file, inps):
    """Read input template options into Namespace inps"""
    print('read input option from template file: ' + template_file)
    if not inps:
        inps = cmd_line_parse()
    inps_dict = vars(inps)
    template = readfile.read_template(template_file)
    template = ut.check_template_auto_value(template)

    prefix = 'pysar.geocode.'
    key_list = [i for i in list(inps_dict.keys()) if prefix + i in template.keys()]
    for key in key_list:
        value = template[prefix + key]
        if value:
            if key == 'SNWE':
                inps_dict[key] = tuple([float(i) for i in value.split(',')])
            elif key in ['latStep', 'lonStep']:
                inps_dict[key] = float(value)
            elif key in ['interpMethod']:
                inps_dict[key] = value
            elif key == 'fillValue':
                if 'nan' in value.lower():
                    inps_dict[key] = np.nan
                else:
                    inps_dict[key] = float(value)

    inps.laloStep = [inps.latStep, inps.lonStep]
    if None in inps.laloStep:
        inps.laloStep = None
    return inps


############################################################################################
def metadata_radar2geo(atr_in, res_obj, print_msg=True):
    """update metadata for radar to geo coordinates"""
    atr = dict(atr_in)
    atr['LENGTH'] = res_obj.length
    atr['WIDTH'] = res_obj.width
    atr['Y_FIRST'] = res_obj.SNWE[1]
    atr['X_FIRST'] = res_obj.SNWE[2]
    atr['Y_STEP'] = res_obj.laloStep[0]
    atr['X_STEP'] = res_obj.laloStep[1]
    atr['Y_UNIT'] = 'degrees'
    atr['X_UNIT'] = 'degrees'

    # Reference point from y/x to lat/lon
    if 'REF_Y' in atr.keys():
        ref_lat, ref_lon = ut.radar2glob(np.array(int(atr['REF_Y'])), np.array(int(atr['REF_X'])),
                                         res_obj.file, atr_in, print_msg=False)[0:2]
        if ~np.isnan(ref_lat) and ~np.isnan(ref_lon):
            ref_y = int(np.rint((ref_lat - float(atr['Y_FIRST'])) / float(atr['Y_STEP'])))
            ref_x = int(np.rint((ref_lon - float(atr['X_FIRST'])) / float(atr['X_STEP'])))
            atr['REF_LAT'] = str(ref_lat)
            atr['REF_LON'] = str(ref_lon)
            atr['REF_Y'] = str(ref_y)
            atr['REF_X'] = str(ref_x)
            if print_msg:
                print('update REF_LAT/LON/Y/X')
        else:
            warnings.warn("original reference pixel is out of .trans file's coverage. Continue.")
            try:
                atr.pop('REF_Y')
                atr.pop('REF_X')
            except:
                pass
            try:
                atr.pop('REF_LAT')
                atr.pop('REF_LON')
            except:
                pass
    return atr


def metadata_geo2radar(atr_in, res_obj, print_msg=True):
    """update metadata for geo to radar coordinates"""
    atr = dict(atr_in)
    atr['LENGTH'] = res_obj.length
    atr['WIDTH'] = res_obj.width
    for i in ['Y_FIRST', 'X_FIRST', 'Y_STEP', 'X_STEP', 'Y_UNIT', 'X_UNIT',
              'REF_Y', 'REF_X', 'REF_LAT', 'REF_LON']:
        try:
            atr.pop(i)
        except:
            pass
    return atr


def resample_data(data, inps, res_obj):
    """resample 2D/3D data"""
    if len(data.shape) == 3:
        data = np.moveaxis(data, 0, -1)

    # resample source data into target data
    geo_data = res_obj.resample(src_data=data, interp_method=inps.interpMethod, fill_value=inps.fillValue,
                                nprocs=inps.nprocs, print_msg=False)

    if len(geo_data.shape) == 3:
        geo_data = np.moveaxis(geo_data, -1, 0)
    return geo_data


def auto_output_filename(infile, inps):
    if len(inps.file) == 1 and inps.outfile:
        return inps.outfile

    if inps.radar2geo:
        prefix = 'geo_'
    else:
        prefix = 'rdr_'

    if inps.dset:
        outfile = '{}{}.h5'.format(prefix, inps.dset)
    else:
        outfile = '{}{}'.format(prefix, os.path.basename(infile))

    if inps.out_dir:
        outfile = os.path.join(inps.out_dir, outfile)
    return outfile


def run_resample(inps):
    """resample all input files"""
    start_time = time.time()

    # Prepare geometry for geocoding
    res_obj = resample(lookupFile=inps.lookupFile, dataFile=inps.file[0],
                       SNWE=inps.SNWE, laloStep=inps.laloStep)
    res_obj.get_geometry_definition()

    inps.nprocs = multiprocessing.cpu_count()

    # resample input files one by one
    for infile in inps.file:
        print('-' * 50+'\nresampling file: {}'.format(infile))
        outfile = auto_output_filename(infile, inps)
        if inps.updateMode and not ut.update_file(outfile, [infile, inps.lookupFile]):
            print('update mode is ON, skip geocoding.')
            return outfile

        # read source data and resample
        dsNames = readfile.get_dataset_list(infile, datasetName=inps.dset)
        maxDigit = max([len(i) for i in dsNames])
        dsResDict = dict()
        for dsName in dsNames:
            print('resampling {d:<{w}} from {f} using {n} processor cores ...'.format(
                d=dsName, w=maxDigit, f=os.path.basename(infile), n=inps.nprocs))
            data = readfile.read(infile, datasetName=dsName, print_msg=False)[0]
            res_data = resample_data(data, inps, res_obj)
            dsResDict[dsName] = res_data

        # update metadata
        atr = readfile.read_attribute(infile, datasetName=inps.dset)
        if inps.radar2geo:
            atr = metadata_radar2geo(atr, res_obj)
        else:
            atr = metadata_geo2radar(atr, res_obj)
        if len(dsNames) == 1 and dsName not in ['timeseries']:
            atr['FILE_TYPE'] = dsNames[0]
            infile = None

        writefile.write(dsResDict, out_file=outfile, metadata=atr, ref_file=infile)

    m, s = divmod(time.time()-start_time, 60)
    print('\ntime used: {:02.0f} mins {:02.1f} secs\nDone.'.format(m, s))
    return outfile


######################################################################################
def main(iargs=None):
    inps = cmd_line_parse(iargs)
    if inps.templateFile:
        inps = read_template2inps(inps.templateFile, inps)
    inps = _check_inps(inps)

    run_resample(inps)
    return


######################################################################################
if __name__ == '__main__':
    main()
