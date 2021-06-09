"""This module provides functions to format several catalogues

These are:
    - eboss_convert_dla
    - desi_convert_dla
    - desi_from_truth_to_drq
    - desi_from_ztarget_to_drq
    - desi_convert_transmission_to_delta_files
    - desi_convert_delta_files_from_true_cont
See the respective docstrings for more details
"""
import os
import sys
import glob
import numpy as np
import fitsio
import healpy
from scipy.interpolate import interp1d

from picca.data import Delta, Forest
from picca.utils import userprint

from picca import prep_del, io, constants

def eboss_convert_dla(in_path, drq_filename, out_path, drq_z_key='Z'):
    """Converts Pasquier Noterdaeme ASCII DLA catalog to a fits file

    Args:
        in_path: string
            Full path filename containing the ASCII DLA catalogue
        drq_filename: string
            Filename of the DRQ catalogue
        out_path: string
            Full path filename where the fits DLA catalogue will be written to
        drq_z_key: string
            Name of the column of DRQ containing the quasrs redshift
    """
    # Read catalogue
    filename = open(os.path.expandvars(in_path), 'r')
    for line in filename:
        cols = line.split()
        if (len(cols) == 0) or (cols[0][0] == '#') or (cols[0][0] == '-'):
            continue
        if cols[0] == 'ThingID':
            from_key_to_index = {key: index for index, key in enumerate(cols)}
            dla_cat = {key: [] for key in from_key_to_index}
            for key in 'MJD-plate-fiber'.split('-'):
                dla_cat[key] = []
            continue
        for key in from_key_to_index.keys():
            value = cols[from_key_to_index[key]]
            if key == 'MJD-plate-fiber':
                for key2, value2 in zip('MJD-plate-fiber'.split('-'),
                                        value.split('-')):
                    dla_cat[key2] += [value2]
            dla_cat[key] += [value]
    filename.close()
    userprint(("INFO: Found {} DLA from {} "
               "quasars").format(len(dla_cat['ThingID']),
                                 np.unique(dla_cat['ThingID']).size))

    # convert Noterdaemem keys to picca keys
    from_noterdaeme_key_to_picca_key = {
        'ThingID': 'THING_ID',
        'z_abs': 'Z',
        'zqso': 'ZQSO',
        'NHI': 'NHI',
        'plate': 'PLATE',
        'MJD': 'MJD',
        'fiber': 'FIBERID',
        'RA': 'RA',
        'Dec': 'DEC'
    }
    # define types
    from_picca_key_to_type = {
        'THING_ID': np.int64,
        'Z': np.float64,
        'ZQSO': np.float64,
        'NHI': np.float64,
        'PLATE': np.int64,
        'MJD': np.int64,
        'FIBERID': np.int64,
        'RA': np.float64,
        'DEC': np.float64
    }

    # format catalogue
    cat = {
        value: np.array(dla_cat[key], dtype=from_picca_key_to_type[value])
        for key, value in from_noterdaeme_key_to_picca_key.items()
    }

    # apply cuts
    w = cat['THING_ID'] > 0
    userprint(("INFO: Removed {} DLA, because "
               "THING_ID<=0").format((cat['THING_ID'] <= 0).sum()))
    w &= cat['Z'] > 0.
    userprint(("INFO: Removed {} DLA, because "
               "Z<=0.").format((cat['Z'] <= 0.).sum()))
    for key in cat:
        cat[key] = cat[key][w]

    # update RA, DEC, and Z_QSO from DRQ catalogue
    hdul = fitsio.FITS(drq_filename)
    thingid = hdul[1]['THING_ID'][:]
    ra = hdul[1]['RA'][:]
    dec = hdul[1]['DEC'][:]
    z_qso = hdul[1][drq_z_key][:]
    hdul.close()
    from_thingid_to_index = {t: index for index, t in enumerate(thingid)}
    cat['RA'] = np.array(
        [ra[from_thingid_to_index[t]] for t in cat['THING_ID']])
    cat['DEC'] = np.array(
        [dec[from_thingid_to_index[t]] for t in cat['THING_ID']])
    cat['ZQSO'] = np.array(
        [z_qso[from_thingid_to_index[t]] for t in cat['THING_ID']])

    # apply cuts
    w = cat['RA'] != cat['DEC']
    userprint(("INFO: Removed {} DLA, because "
               "RA==DEC").format((cat['RA'] == cat['DEC']).sum()))
    w &= cat['RA'] != 0.
    userprint(("INFO: Removed {} DLA, because "
               "RA==0").format((cat['RA'] == 0.).sum()))
    w &= cat['DEC'] != 0.
    userprint(("INFO: Removed {} DLA, because "
               "DEC==0").format((cat['DEC'] == 0.).sum()))
    w &= cat['ZQSO'] > 0.
    userprint(("INFO: Removed {} DLA, because "
               "ZQSO<=0.").format((cat['ZQSO'] <= 0.).sum()))
    for key in cat:
        cat[key] = cat[key][w]

    # sort first by redshift
    w = np.argsort(cat['Z'])
    for key in cat.keys():
        cat[key] = cat[key][w]
    # then by thingid
    w = np.argsort(cat['THING_ID'])
    for key in cat:
        cat[key] = cat[key][w]
    # add DLA ID
    cat['DLAID'] = np.arange(1, cat['Z'].size + 1, dtype=np.int64)

    for key in ['RA', 'DEC']:
        cat[key] = cat[key].astype('float64')

    # Save catalogue
    results = fitsio.FITS(out_path, 'rw', clobber=True)
    cols = list(cat.values())
    names = list(cat)
    results.write(cols, names=names, extname='DLACAT')
    results.close()


def desi_convert_dla(in_path, out_path):
    """Convert a catalog of DLA from a DESI format to the format used by picca

    Args:
        in_path: string
            Full path filename containing the ASCII DLA catalogue
        out_path: string
            Full path filename where the fits DLA catalogue will be written to
    """
    from_desi_key_to_picca_key = {
        'RA': 'RA',
        'DEC': 'DEC',
        'Z': 'Z_DLA_RSD',
        'ZQSO': 'Z_QSO_RSD',
        'NHI': 'N_HI_DLA',
        'THING_ID': 'MOCKID',
        'DLAID': 'DLAID',
        'PLATE': 'MOCKID',
        'MJD': 'MOCKID',
        'FIBERID': 'MOCKID',
    }
    # read catalogue
    cat = {}
    hdul = fitsio.FITS(in_path)
    for key, value in from_desi_key_to_picca_key.items():
        cat[key] = hdul['DLACAT'][value][:]
    hdul.close()
    userprint(("INFO: Found {} DLA from {} "
               "quasars").format(cat['Z'].size,
                                 np.unique(cat['THING_ID']).size))
    # sort by THING_ID
    w = np.argsort(cat['THING_ID'])
    for key in cat:
        cat[key] = cat[key][w]

    for key in ['RA', 'DEC']:
        cat[key] = cat[key].astype('float64')

    # save results
    results = fitsio.FITS(out_path, 'rw', clobber=True)
    cols = list(cat.values())
    names = list(cat)
    results.write(cols, names=names, extname='DLACAT')
    results.close()


def desi_from_truth_to_drq(truth_filename,
                           targets_filename,
                           out_path,
                           spec_type="QSO"):
    """Transform a desi truth.fits file and a desi targets.fits into a drq
    like file

    Args:
        truth_filename: string
            Filename of the truth.fits file
        targets_filename: string
            Filename of the desi targets.fits file
        out_path: string
            Full path filename where the fits catalogue will be written to
        spec_type: string
            Spectral type of the objects to include in the catalogue
    """
    # read truth table
    hdul = fitsio.FITS(truth_filename)

    # apply cuts
    w = np.ones(hdul[1]['TARGETID'][:].size).astype(bool)
    userprint(" start                 : nb object in cat = {}".format(w.sum()))
    w &= np.char.strip(hdul[1]['TRUESPECTYPE'][:].astype(str)) == spec_type
    userprint(" and TRUESPECTYPE=={}  : nb object in cat = {}".format(
        spec_type, w.sum()))
    # load the arrays
    thingid = hdul[1]['TARGETID'][:][w]
    z_qso = hdul[1]['TRUEZ'][:][w]
    hdul.close()
    ra = np.zeros(thingid.size)
    dec = np.zeros(thingid.size)
    plate = thingid
    mjd = thingid
    fiberid = thingid

    ### Get RA and DEC from targets
    hdul = fitsio.FITS(targets_filename)
    thingid_targets = hdul[1]['TARGETID'][:]
    ra_targets = hdul[1]['RA'][:].astype('float64')
    dec_targets = hdul[1]['DEC'][:].astype('float64')
    hdul.close()

    from_targetid_to_index = {}
    for index, t in enumerate(thingid_targets):
        from_targetid_to_index[t] = index
    keys_from_targetid_to_index = from_targetid_to_index.keys()

    for index, t in enumerate(thingid):
        if t not in keys_from_targetid_to_index:
            continue
        index2 = from_targetid_to_index[t]
        ra[index] = ra_targets[index2]
        dec[index] = dec_targets[index2]

    # apply cuts
    if (ra == 0.).sum() != 0 or (dec == 0.).sum() != 0:
        w = ra != 0.
        w &= dec != 0.
        userprint((" and RA and DEC        : nb object in cat = "
                   "{}").format(w.sum()))

        ra = ra[w]
        dec = dec[w]
        z_qso = z_qso[w]
        thingid = thingid[w]
        plate = plate[w]
        mjd = mjd[w]
        fiberid = fiberid[w]

    # save catalogue
    results = fitsio.FITS(out_path, 'rw', clobber=True)
    cols = [ra, dec, thingid, plate, mjd, fiberid, z_qso]
    names = ['RA', 'DEC', 'THING_ID', 'PLATE', 'MJD', 'FIBERID', 'Z']
    results.write(cols, names=names, extname='CAT')
    results.close()


def desi_from_ztarget_to_drq(in_path,
                             out_path,
                             spec_type='QSO',
                             downsampling_z_cut=None,
                             downsampling_num=None):
    """Transforms a catalog of object in desi format to a catalog in DRQ format

    Args:
        in_path: string
            Full path filename containing the catalogue of objects
        out_path: string
            Full path filename where the fits DLA catalogue will be written to
        spec_type: string
            Spectral type of the objects to include in the catalogue
        downsampling_z_cut: float or None - default: None
            Minimum redshift to downsample the data. 'None' for no downsampling
        downsampling_num: int
            Target number of object above redshift downsampling-z-cut.
            'None' for no downsampling
    """

    ## Info of the primary observation
    hdul = fitsio.FITS(in_path)
    spec_type_list = np.char.strip(hdul[1]['SPECTYPE'][:].astype(str))

    # apply cuts
    userprint((" start               : nb object in cat = "
               "{}").format(spec_type_list.size))
    w = hdul[1]['ZWARN'][:] == 0.
    userprint(' and zwarn==0        : nb object in cat = {}'.format(w.sum()))
    w &= spec_type_list == spec_type
    userprint(' and spectype=={}    : nb object in cat = {}'.format(
        spec_type, w.sum()))
    # load the arrays
    cat = {}
    from_desi_key_to_picca_key = {
        'RA': 'RA',
        'DEC': 'DEC',
        'Z': 'Z',
        'THING_ID': 'TARGETID',
        'PLATE': 'TARGETID',
        'MJD': 'TARGETID',
        'FIBERID': 'TARGETID'
    }
    for key, value in from_desi_key_to_picca_key.items():
        cat[key] = hdul[1][value][:][w]
    hdul.close()

    for key in ['RA', 'DEC']:
        cat[key] = cat[key].astype('float64')

    # apply downsampling
    if downsampling_z_cut is not None and downsampling_num is not None:
        if cat['RA'].size < downsampling_num:
            userprint(("WARNING:: Trying to downsample, when nb cat = {} and "
                       "nb downsampling = {}").format(cat['RA'].size,
                                                      downsampling_num))
        else:
            z_cut_num = (cat['Z'] > downsampling_z_cut).sum()
            select_fraction = (downsampling_num / z_cut_num)
            if select_fraction < 1.0:
                np.random.seed(0)
                w = np.random.choice(np.arange(cat['RA'].size),
                                 size=int(cat['RA'].size * select_fraction),
                                 replace=False)
                for key in cat:
                    cat[key] = cat[key][w]
                userprint((" and downsampling : nb object in cat = {}, nb z > "
                       "{} = {}").format(cat['RA'].size, downsampling_z_cut,
                                        z_cut_num))
            else:
                userprint(("WARNING:: Trying to downsample, when nb QSOs with "
                           "z > {} = {} and downsampling = {}").format
                           (downsampling_z_cut, z_cut_num, downsampling_num))

    # sort by THING_ID
    w = np.argsort(cat['THING_ID'])
    for key in cat:
        cat[key] = cat[key][w]

    # save catalogue
    results = fitsio.FITS(out_path, 'rw', clobber=True)
    cols = list(cat.values())
    names = list(cat)
    results.write(cols, names=names, extname='CAT')
    results.close()


def desi_convert_transmission_to_delta_files(obj_path,
                                             out_dir,
                                             in_dir=None,
                                             in_filenames=None,
                                             lambda_min=3600.,
                                             lambda_max=5500.,
                                             lambda_min_rest_frame=1040.,
                                             lambda_max_rest_frame=1200.,
                                             delta_log_lambda=3.e-4,
                                             max_num_spec=None,
                                             use_constant_weight=False,):
    """Convert desi transmission files to picca delta files

    Args:
        obj_path: str
            Path to the catalog of object to extract the transmission from
        out_dir: str
            Path to the directory where delta files will be written
        in_dir: str or None - default: None
            Path to the directory containing the transmission files directory.
            If 'None', then in_files must be a non-empty array
        in_filenames: array of str or None - default: None
            List of the filenames for the transmission files. Ignored if in_dir
            is not 'None'
        lambda_min: float - default: 3600.
            Minimum observed wavelength in Angstrom
        lambda_max: float - default: 5500.
            Maximum observed wavelength in Angstrom
        lambda_min_rest_frame: float - default: 1040.
            Minimum Rest Frame wavelength in Angstrom
        lambda_max_rest_frame: float - default: 1200.
            Maximum Rest Frame wavelength in Angstrom
        delta_log_lambda: float - default: 3.e-4
            Variation of the logarithm of the wavelength between two pixels
        max_num_spec: int or None - default: None
            Maximum number of spectra to read. 'None' for no maximum
    """
    # read catalog of objects
    hdul = fitsio.FITS(obj_path)
    key_val = np.char.strip(
        np.array([
            hdul[1].read_header()[key] for key in hdul[1].read_header().keys()
        ]).astype(str))
    if 'TARGETID' in key_val:
        objs_thingid = hdul[1]['TARGETID'][:]
    elif 'THING_ID' in key_val:
        objs_thingid = hdul[1]['THING_ID'][:]
    w = hdul[1]['Z'][:] > max(0., lambda_min / lambda_max_rest_frame - 1.)
    w &= hdul[1]['Z'][:] < max(0., lambda_max / lambda_min_rest_frame - 1.)
    objs_ra = hdul[1]['RA'][:][w].astype('float64') * np.pi / 180.
    objs_dec = hdul[1]['DEC'][:][w].astype('float64') * np.pi / 180.
    objs_thingid = objs_thingid[w]
    hdul.close()
    userprint('INFO: Found {} quasars'.format(objs_ra.size))

    # Load list of transmission files
    if ((in_dir is None and in_filenames is None) or
            (in_dir is not None and in_filenames is not None)):
        userprint(("ERROR: No transmisson input files or both 'in_dir' and "
                   "'in_filenames' given"))
        sys.exit()
    elif in_dir is not None:
        files = glob.glob(in_dir + '/*/*/transmission*.fits*')
        files = np.sort(np.array(files))
        hdul = fitsio.FITS(files[0])
        in_nside = hdul['METADATA'].read_header()['HPXNSIDE']
        nest = hdul['METADATA'].read_header()['HPXNEST']
        hdul.close()
        in_healpixs = healpy.ang2pix(in_nside,
                                     np.pi / 2. - objs_dec,
                                     objs_ra,
                                     nest=nest)
        if files[0].endswith('.gz'):
            end_of_file = '.gz'
        else:
            end_of_file = ''
        files = np.sort(
            np.array([("{}/{}/{healpix}/transmission-{}-{healpix}"
                       ".fits{}").format(in_dir,
                                         int(healpix // 100),
                                         in_nside,
                                         end_of_file,
                                         healpix=healpix)
                      for healpix in np.unique(in_healpixs)]))
    else:
        files = np.sort(np.array(in_filenames))
    userprint('INFO: Found {} files'.format(files.size))

    # Stack the deltas transmission
    log_lambda_min = np.log10(lambda_min)
    log_lambda_max = np.log10(lambda_max)
    num_bins = int((log_lambda_max - log_lambda_min) / delta_log_lambda) + 1
    stack_trans = np.zeros(num_bins)
    stack_weight = np.zeros(num_bins)

    deltas = {}
    tid = []
    # read deltas
    for index, filename in enumerate(files):
        userprint("\rread {} of {} {}".format(
            index, files.size,
            np.sum([len(deltas[healpix]) for healpix in deltas])),
                  end="")
        hdul = fitsio.FITS(filename)
        thingid = hdul['METADATA']['MOCKID'][:]
        if np.in1d(thingid, objs_thingid).sum() == 0:
            hdul.close()
            continue
        ra = hdul['METADATA']['RA'][:].astype(np.float64) * np.pi / 180.
        dec = hdul['METADATA']['DEC'][:].astype(np.float64) * np.pi / 180.
        z = hdul['METADATA']['Z'][:]
        log_lambda = np.log10(hdul['WAVELENGTH'].read())
        if 'F_LYA' in hdul:
            trans = hdul['F_LYA'].read()
        else:
            trans = hdul['TRANSMISSION'].read()

        num_obj = z.size
        healpix = filename.split('-')[-1].split('.')[0]

        if trans.shape[0] != num_obj:
            trans = trans.transpose()

        bins = np.floor((log_lambda - log_lambda_min) / delta_log_lambda +
                        0.5).astype(int)
        aux_log_lambda = log_lambda_min + bins * delta_log_lambda
        lambda_obs_frame =  (10**aux_log_lambda) * np.ones(num_obj)[:, None]
        lambda_rest_frame = (10**aux_log_lambda) / (1. + z[:, None])
        valid_pixels = np.zeros_like(trans).astype(int)
        valid_pixels[(lambda_obs_frame >= lambda_min) &
                     (lambda_obs_frame < lambda_max) &
                     (lambda_rest_frame > lambda_min_rest_frame) &
                     (lambda_rest_frame < lambda_max_rest_frame)] = 1
        num_pixels = np.sum(valid_pixels, axis=1)
        w = num_pixels >= 50
        w &= np.in1d(thingid, objs_thingid)
        if w.sum() == 0:
            hdul.close()
            continue

        ra = ra[w]
        dec = dec[w]
        z = z[w]
        thingid = thingid[w]
        trans = trans[w, :]
        valid_pixels = valid_pixels[w, :]
        num_obj = z.size
        hdul.close()

        deltas[healpix] = []
        for index2 in range(num_obj):
            tid.append(thingid[index2])
            aux_log_lambda = log_lambda[valid_pixels[index2, :] > 0]
            aux_trans = trans[index2, :][valid_pixels[index2, :] > 0]

            bins = np.floor((aux_log_lambda - log_lambda_min) /
                            delta_log_lambda + 0.5).astype(int)
            rebin_log_lambda = (log_lambda_min +
                                np.arange(num_bins) * delta_log_lambda)
            rebin_flux = np.bincount(bins,
                                     weights=aux_trans,
                                     minlength=num_bins)
            rebin_ivar = np.bincount(bins, minlength=num_bins).astype(float)

            w = rebin_ivar > 0.
            if w.sum() < 50:
                continue
            stack_trans += rebin_flux
            stack_weight += rebin_ivar
            rebin_log_lambda = rebin_log_lambda[w]
            rebin_flux = rebin_flux[w] / rebin_ivar[w]
            rebin_ivar = rebin_ivar[w]
            deltas[healpix].append(
                Delta(thingid[index2], ra[index2], dec[index2], z[index2],
                      thingid[index2], thingid[index2], thingid[index2],
                      rebin_log_lambda, rebin_ivar, None, rebin_flux, 1, None,
                      None, None, None, None, None))
        if (max_num_spec is not None and
                np.sum([len(deltas[healpix])
                        for healpix in deltas]) >= max_num_spec):
            break

    userprint('\n')

    # normalize stacked transmission
    w = stack_weight > 0.
    
    stack_trans[w] /= stack_weight[w]
    
    stack_log_lambda = np.arange(log_lambda_min,log_lambda_max,delta_log_lambda)
    iter_file = fitsio.FITS(out_dir.replace('deltas','')+"/iter.fits.gz",'rw',clobber=True)
    
    iter_file.write([np.array(tid)],
                  names=['THINGID'],
                  extname='THINGID')
    
    header = {}
    header["NSIDE"] = in_nside
    iter_file.write([stack_log_lambda, stack_trans, stack_weight],
                  names=['LOGLAM', 'MEAN_TRANS', 'WEIGHT'],
                  header=header,
                  extname='MEAN_TRANS')
    
    #  save results
    for index, healpix in enumerate(sorted(deltas)):
        if len(deltas[healpix]) == 0:
            userprint('No data in {}'.format(healpix))
            continue
        results = fitsio.FITS(out_dir + '/delta-{}'.format(healpix) +
                              '.fits.gz',
                              'rw',
                              clobber=True)
        for delta in deltas[healpix]:
            bins = np.floor((delta.log_lambda - log_lambda_min) /
                            delta_log_lambda + 0.5).astype(int)
            delta.delta = delta.delta / stack_trans[bins] - 1.
            
            if not use_constant_weight:
                delta.weights *= stack_trans[bins]**2
            else:
                delta.weights = np.ones(len(stack_trans[bins]))

            header = {}
            header['RA'] = delta.ra
            header['DEC'] = delta.dec
            header['Z'] = delta.z_qso
            header['PMF'] = '{}-{}-{}'.format(delta.plate, delta.mjd,
                                              delta.fiberid)
            header['THING_ID'] = delta.thingid
            header['PLATE'] = delta.plate
            header['MJD'] = delta.mjd
            header['FIBERID'] = delta.fiberid
            header['ORDER'] = delta.order

            cols = [
                delta.log_lambda, delta.delta, delta.weights,
                np.ones(delta.log_lambda.size)
            ]
            names = ['LOGLAM', 'DELTA', 'WEIGHT', 'CONT']
            results.write(cols,
                          names=names,
                          header=header,
                          extname=str(delta.thingid))
        results.close()
        userprint("\rwrite {} of {}: {} quasars".format(index, len(deltas),
                                                        len(deltas[healpix])),
                  end="")
        
    stack_log_lambda, stack_delta, stack_weight = stack(deltas,log_lambda_max, log_lambda_min, delta_log_lambda)#, stack_from_deltas=True)
    
    iter_file.write([stack_log_lambda, stack_delta, stack_weight],
                  names=['LOGLAM', 'STACK', 'WEIGHT'],
                  header=header,
                  extname='STACK')
    iter_file.close()

    userprint("")
    
def stack(data,log_lambda_max, log_lambda_min, delta_log_lambda):
    """Computes a stack of the delta field as a function of wavelength
    Args:
        data: dict
            A dictionary with the forests in each healpix. If stack_from_deltas
            is passed, then the dictionary must contain the deltas in each
            healpix
        stack_from_deltas: bool - default: False
            Flag to determine whether to stack from deltas or compute them
    Returns:
        The following variables:
            stack_log_lambda: Logarithm of the wavelengths (in Angs).
            stack_delta: The stacked delta field.
            stack_weight: Total weight on the delta_stack
    """
    num_bins = int((log_lambda_max - log_lambda_min) /
                   delta_log_lambda) + 1
    stack_log_lambda = (log_lambda_min +
                        np.arange(num_bins) * delta_log_lambda)
    stack_delta = np.zeros(num_bins)
    stack_weight = np.zeros(num_bins)
    for healpix in sorted(list(data.keys())):
        for forest in data[healpix]:
            delta = forest.delta
            weights = forest.weights

            bins = ((forest.log_lambda - log_lambda_min) /
                    delta_log_lambda + 0.5).astype(int)
            rebin = np.bincount(bins, weights=delta * weights)
            stack_delta[:len(rebin)] += rebin
            rebin = np.bincount(bins, weights=weights)
            stack_weight[:len(rebin)] += rebin

    w = stack_weight > 0
    stack_delta[w] /= stack_weight[w]

    return stack_log_lambda, stack_delta, stack_weight
    
def desi_convert_delta_files_from_true_cont(obj_path,
                                            out_dir,
                                            in_dir_transmission=None,
                                            in_dir_spectra=None,
                                            in_filenames=None,
                                            lambda_min=3600.,
                                            lambda_max=5500.,
                                            lambda_min_rest_frame=1040.,
                                            lambda_max_rest_frame=1200.,
                                            delta_log_lambda=3.e-4,
                                            max_num_spec=None,
                                            dla_mask = .8,
                                            absorber_mask = 2.5,
                                            delta_format = None,
                                            spall=None,
                                            zqso_min=None,
                                            zqso_max=None,
                                            mode='desi',
                                            keep_bal=False,
                                            bi_max=None,
                                            best_obs=False,
                                            single_exp=False,
                                            npix_min=50,
                                            use_constant_weight=False,
                                            eta_min = .5,
                                            eta_max = 1.5,
                                            vlss_min = .2,
                                            vlss_max = .3,
                                            trans_from_file = None,):
    
    """Get picca delta files from true continuum

    Args:
        obj_path: str
            Path to the catalog of object to extract the transmission from
        out_dir: str
            Path to the directory where delta files will be written
        in_dir_transmission: str or None - default: None
            Path to the directory containing the transmission files directory.
            If 'None', then in_files must be a non-empty array
        in_dir_spectra: str or None - default: None
            Path to the directory containing the spectra files directory.
            If 'None', then compute raw deltas
        in_filenames: array of str or None - default: None
            List of the filenames for the transmission files. Ignored if in_dir
            is not 'None'
        lambda_min: float - default: 3600.
            Minimum observed wavelength in Angstrom
        lambda_max: float - default: 5500.
            Maximum observed wavelength in Angstrom
        lambda_min_rest_frame: float - default: 1040.
            Minimum Rest Frame wavelength in Angstrom
        lambda_max_rest_frame: float - default: 1200.
            Maximum Rest Frame wavelength in Angstrom
        delta_log_lambda: float - default: 3.e-4
            Variation of the logarithm of the wavelength between two pixels
        max_num_spec: int or None - default: None
            Maximum number of spectra to read. 'None' for no maximum
    """
    
    if max_num_spec:
        if max_num_spec <= 400:
            print('WARNING: Not enough spectra division by zero will be encountered')
    
    # setup forest class variables
    Forest.log_lambda_min = np.log10(lambda_min)
    Forest.log_lambda_max = np.log10(lambda_max)
    Forest.log_lambda_min_rest_frame = np.log10(lambda_min_rest_frame)
    Forest.log_lambda_max_rest_frame = np.log10(lambda_max_rest_frame)
    Forest.delta_log_lambda = delta_log_lambda
    # minumum dla transmission
    Forest.dla_mask_limit = dla_mask
    Forest.absorber_mask_width = absorber_mask

    # Find the redshift range
    if zqso_min is None:
        zqso_min = max(0., lambda_min / lambda_max_rest_frame - 1.)
        userprint("zqso_min = {}".format(zqso_min))
    if zqso_max is None:
        zqso_max = max(0., lambda_max / lambda_min_rest_frame - 1.)
        userprint("zqso_max = {}".format(zqso_max))

    log_lambda_temp = (Forest.log_lambda_min + np.arange(2) *
                       (Forest.log_lambda_max - Forest.log_lambda_min))
    log_lambda_rest_frame_temp = (
        Forest.log_lambda_min_rest_frame + np.arange(2) *
        (Forest.log_lambda_max_rest_frame - Forest.log_lambda_min_rest_frame))
    
    
    log_file = open(os.path.expandvars(out_dir.replace('deltas','')+'/input.log'), 'w')
    # Read data
    (data, num_data, nside,
     healpy_pix_ordering) = io.read_data(os.path.expandvars(in_dir_spectra),
                                         obj_path,
                                         mode,
                                         z_min=zqso_min,
                                         z_max=zqso_max,
                                         max_num_spec=max_num_spec,
                                         log_file=log_file,
                                         keep_bal=keep_bal,
                                         bi_max=bi_max,
                                         best_obs=best_obs,
                                         single_exp=single_exp,
                                         pk1d=delta_format,
                                         spall=spall)
    
    ## make cuts
    remove_keys = []
    remove_tid = []
    for healpix in data:
        forests = []
        for forest in data[healpix]:
            if ((forest.log_lambda is None)
                    or len(forest.log_lambda) < npix_min):
                log_file.write(("INFO: Rejected {} due to forest too "
                                "short\n").format(forest.thingid))
                remove_tid.append(forest.thingid)
                continue

            if np.isnan((forest.flux * forest.ivar).sum()):
                log_file.write(("INFO: Rejected {} due to nan "
                                "found\n").format(forest.thingid))
                remove_tid.append(forest.thingid)
                continue

            if (use_constant_weight
                    and (forest.flux.mean() <= 0.0 or forest.mean_snr <= 1.0)):
                log_file.write(("INFO: Rejected {} due to negative mean or "
                                "too low SNR found\n").format(forest.thingid))
                remove_tid.append(forest.thingid)
                continue

            forests.append(forest)
            log_file.write("{} {}-{}-{} accepted\n".format(
                forest.thingid, forest.plate, forest.mjd, forest.fiberid))
        data[healpix][:] = forests
        if len(data[healpix]) == 0:
            remove_keys += [healpix]

    for healpix in remove_keys:
        del data[healpix]
      
    ### get mean transmission
    # read catalog of objects
    
    try:
        hdul = fitsio.FITS(out_dir.replace('deltas','')+'/zcat_mean_trans.fits')
    except: ## create new zcat with only tids included in deltas to calculated correct mean transmission
        hdu = fitsio.FITS(obj_path)

        hdout = fitsio.FITS(out_dir.replace('deltas','')+'/zcat_mean_trans.fits', 'rw', clobber = True)

        cat_tid = hdu['CAT']['THING_ID'][:]
        w = np.isin(cat_tid,remove_tid) 

        colsout = []
        for c in hdu[1].get_colnames():
            colsout.append(hdu[1][c][:][~w])

        colnames = ['RA', 'DEC', 'Z', 'THING_ID', 'PLATE', 'MJD', 'FIBERID']

        hdout.write(colsout, names=colnames, extname='CAT')
        hdu.close()
        hdout.close()
        
        hdul = fitsio.FITS(out_dir.replace('deltas','')+'/zcat_mean_trans.fits')

    num_forests = np.sum([len(forest) for forest in data.values()])
    log_file.write(("INFO: Remaining sample has {} "
                        "forests\n").format(num_forests))
    userprint(f"Remaining sample has {num_forests} forests")
        
    key_val = np.char.strip(
        np.array([
            hdul[1].read_header()[key] for key in hdul[1].read_header().keys()
        ]).astype(str))
    if 'TARGETID' in key_val:
        objs_thingid = hdul[1]['TARGETID'][:]
    elif 'THING_ID' in key_val:
        objs_thingid = hdul[1]['THING_ID'][:]
    w = hdul[1]['Z'][:] > max(0., lambda_min / lambda_max_rest_frame - 1.)
    w &= hdul[1]['Z'][:] < max(0., lambda_max / lambda_min_rest_frame - 1.)
    objs_ra = hdul[1]['RA'][:][w].astype('float64') * np.pi / 180.
    objs_dec = hdul[1]['DEC'][:][w].astype('float64') * np.pi / 180.
    objs_thingid = objs_thingid[w]
    hdul.close()
    userprint('INFO: Found {} quasars'.format(objs_ra.size))
    
    log_lambda_min = np.log10(lambda_min)
    log_lambda_max = np.log10(lambda_max)
    num_bins = int((log_lambda_max - log_lambda_min) / delta_log_lambda) + 1
    
    if trans_from_file and not max_num_spec :
        hdu_trans = fitsio.FITS(trans_from_file)
        stack_log_lambda = hdu_trans['MEAN_TRANS']['LOGLAM'][:]
        stack_trans = hdu_trans['MEAN_TRANS']['MEAN_TRANS'][:]
        stack_weight = hdu_trans['MEAN_TRANS']['WEIGHT'][:]
        
        head = hdu_trans['MEAN_TRANS'].read_header()
        in_nside = head['NSIDE']
    
    else:
        # Load list of transmission files
        if ((in_dir_transmission is None and in_filenames is None) or
            (in_dir_transmission is not None and in_filenames is not None)):
            userprint(("ERROR: No spectra input files or both 'in_dir' and ""'in_filenames' given"))
            sys.exit()
        elif in_dir_transmission is not None:
            files = glob.glob(in_dir_transmission + '/*/*/transmission-16-*.fits*')
            files = np.sort(np.array(files))
            hdul = fitsio.FITS(files[0])
            in_nside = hdul['METADATA'].read_header()['HPXNSIDE']
            nest = hdul['METADATA'].read_header()['HPXNEST']
            hdul.close()
            in_healpixs = healpy.ang2pix(in_nside,
                                         np.pi / 2. - objs_dec,
                                         objs_ra,
                                         nest=nest)
            if files[0].endswith('.gz'):
                end_of_file = '.gz'
            else:
                end_of_file = ''
            files_trans = np.sort(
                np.array([("{}/{}/{healpix}/transmission-{}-{healpix}"
                           ".fits{}").format(in_dir_transmission,
                                             int(healpix // 100),
                                             in_nside,
                                             end_of_file,
                                             healpix=healpix)
                          for healpix in np.unique(in_healpixs)])) ### list of all files to read
        else:
            files_trans = np.sort(np.array(in_filenames))
        userprint('INFO: Found {} files'.format(files.size))

        # Stack the deltas transmission
        '''log_lambda_min = np.log10(lambda_min)
        log_lambda_max = np.log10(lambda_max)
        num_bins = int((log_lambda_max - log_lambda_min) / delta_log_lambda) + 1'''
        stack_trans = np.zeros(num_bins)
        stack_weight = np.zeros(num_bins)

        spec_count = 0
        tid = []
        for index, filename in enumerate(files_trans):
            userprint("\rread {} of {} {}".format(index, files.size,spec_count),end="")
            hdul = fitsio.FITS(filename)
            truth = fitsio.FITS(filename.replace("spectra","truth"))
            thingid = hdul['METADATA']['MOCKID'][:]
            if np.in1d(thingid, objs_thingid).sum() == 0:
                print(filename)
                hdul.close()
                continue
            ra = hdul['METADATA']['RA'][:].astype(np.float64) * np.pi / 180.
            dec = hdul['METADATA']['DEC'][:].astype(np.float64) * np.pi / 180.
            z = hdul['METADATA']['Z'][:]
            log_lambda = np.log10(hdul['WAVELENGTH'].read())
            if 'F_LYA' in hdul:
                trans = hdul['F_LYA'].read()
            else:
                trans = hdul['TRANSMISSION'].read()

            num_obj = z.size
            healpix = filename.split('-')[-1].split('.')[0]

            if trans.shape[0] != num_obj:
                trans = trans.transpose()

            bins = np.floor((log_lambda - log_lambda_min) / delta_log_lambda +
                            0.5).astype(int)
            aux_log_lambda = log_lambda_min + bins * delta_log_lambda
            lambda_obs_frame =  (10**aux_log_lambda) * np.ones(num_obj)[:, None]
            lambda_rest_frame = (10**aux_log_lambda) / (1. + z[:, None])
            valid_pixels = np.zeros_like(trans).astype(int)
            valid_pixels[(lambda_obs_frame >= lambda_min) &
                         (lambda_obs_frame < lambda_max) &
                         (lambda_rest_frame > lambda_min_rest_frame) &
                         (lambda_rest_frame < lambda_max_rest_frame)] = 1
            num_pixels = np.sum(valid_pixels, axis=1)
            w = num_pixels >= 50
            w &= np.in1d(thingid, objs_thingid)
            if w.sum() == 0:
                hdul.close()
                continue

            ra = ra[w]
            dec = dec[w]
            z = z[w]
            thingid = thingid[w]
            trans = trans[w, :]
            valid_pixels = valid_pixels[w, :]
            num_obj = z.size
            hdul.close()

            for index2 in range(num_obj):
                tid.append(thingid[index2])
                aux_log_lambda = log_lambda[valid_pixels[index2, :] > 0]
                aux_trans = trans[index2, :][valid_pixels[index2, :] > 0]

                bins = np.floor((aux_log_lambda - log_lambda_min) /delta_log_lambda + 0.5).astype(int)
                rebin_log_lambda = (log_lambda_min +np.arange(num_bins) * delta_log_lambda)
                rebin_flux = np.bincount(bins,weights=aux_trans,minlength=num_bins)
                rebin_ivar = np.bincount(bins, minlength=num_bins).astype(float)

                w = rebin_ivar > 0.
                if w.sum() < npix_min:
                    continue
                stack_trans += rebin_flux
                stack_weight += rebin_ivar
                rebin_log_lambda = rebin_log_lambda[w]
                rebin_flux = rebin_flux[w] / rebin_ivar[w]
                rebin_ivar = rebin_ivar[w]

                spec_count+=1
            if (max_num_spec is not None and  spec_count >= max_num_spec):
                break
        userprint('\n')
        # normalize stacked transmission
        w = stack_weight > 0.

        stack_trans[w] /= stack_weight[w]
    
        stack_log_lambda = np.arange(log_lambda_min,log_lambda_max,delta_log_lambda)
    
    results = fitsio.FITS(out_dir.replace('deltas','')+"/iter.fits.gz",'rw',clobber=True)

    if not trans_from_file:
        results.write([np.array(tid)],
                      names=['THINGID'],
                      extname='THINGID_TRANS')
    
    header = {}
    header["NSIDE"] = in_nside
    results.write([stack_log_lambda, stack_trans, stack_weight],
                  names=['LOGLAM', 'MEAN_TRANS', 'WEIGHT'],
                  header=header,
                  extname='MEAN_TRANS')
    ### end of mean transmission

    Forest.get_var_lss = interp1d(log_lambda_temp,0.2 + np.zeros(2),fill_value="extrapolate",kind="nearest")
    Forest.get_eta = interp1d(log_lambda_temp,np.ones(2),fill_value="extrapolate",kind="nearest")
    Forest.get_fudge = interp1d(log_lambda_temp,np.zeros(2),fill_value="extrapolate",kind="nearest")
    Forest.get_mean_cont = interp1d(log_lambda_rest_frame_temp,1 + np.zeros(2))
    
    # add continua to forest objects
    tids_list = []
    for healpix in data:
        for forest in data[healpix]:
            tid = forest.thingid
            tids_list.append(tid)
            pix = healpy.ang2pix(in_nside, np.pi / 2. - forest.dec, forest.ra, nest=True)

            truth = fitsio.FITS(in_dir_spectra+f'/{pix//100}/{pix}/truth-16-{pix}.fits') ## find corresponding truth file

            ind = np.where(truth['TRUTH']['TARGETID'][:] == tid)[0][0]
            cont = truth[3]['TRUE_CONT'][ind]
            head = truth[3].read_header()
            truth.close()
            
            wave_cont = np.log10(np.arange(head['WMIN'],head['WMAX']+head['DWAVE']/2,head['DWAVE']))
            cont_rebin = np.interp(forest.log_lambda, wave_cont, cont)

            forest.continuum = cont_rebin
            #forest.cont = cont_rebin

            bins = np.floor((forest.log_lambda - log_lambda_min) /
                            delta_log_lambda + 0.5).astype(int)
            
            forest.cont = stack_trans[bins] * forest.continuum
            #mean_flux = stack_trans[bins] * forest.cont
            
            forest.delta = forest.flux / forest.cont - 1.
            #forest.delta = forest.flux / mean_flux - 1.

            forest.mean_trans = stack_trans[bins]
            
    if not use_constant_weight:
        
        (log_lambda_rest_frame, mean_cont,mean_cont_weight) = prep_del.compute_mean_cont(data)
        w = mean_cont_weight > 0.
        log_lambda_cont = log_lambda_rest_frame[w]
        new_cont = Forest.get_mean_cont(log_lambda_cont) * mean_cont[w]
        Forest.get_mean_cont = interp1d(log_lambda_cont,new_cont,fill_value="extrapolate")

        (log_lambda, eta, var_lss, fudge, num_pixels, var_pipe_values,
                 var_delta, var2_delta, count, num_qso, chi2_in_bin, error_eta,
                 error_var_lss, error_fudge) = prep_del.compute_var_stats(
                     data, (eta_min, eta_max),
                     (vlss_min, vlss_max), fix_eta_fudge = True)
        w = num_pixels > 0

        Forest.get_eta = interp1d(log_lambda[w],eta[w],fill_value="extrapolate",kind="nearest")
        Forest.get_var_lss = interp1d(log_lambda[w],var_lss[w],fill_value="extrapolate",kind="nearest")
        Forest.get_fudge = interp1d(log_lambda[w],fudge[w],fill_value="extrapolate",kind="nearest")
        
    else:
        num_bins = 10  # this value is arbitrary
        log_lambda = (
            Forest.log_lambda_min + (np.arange(num_bins) + .5) *
            (Forest.log_lambda_max - Forest.log_lambda_min) / num_bins)

        userprint(("INFO: using constant weights, skipping eta, var_lss, fudge fits"))
        eta = np.zeros(num_bins)
        var_lss = np.ones(num_bins)
        fudge = np.zeros(num_bins)

        Forest.get_eta = interp1d(log_lambda,eta,fill_value='extrapolate',kind='nearest')
        Forest.get_var_lss = interp1d(log_lambda,var_lss,fill_value='extrapolate',kind='nearest')
        Forest.get_fudge = interp1d(log_lambda,fudge,fill_value='extrapolate',kind='nearest')
    
    # calculate correct weights
    for healpix in data:
        for forest in data[healpix]:

            bins = np.floor((forest.log_lambda - log_lambda_min) / delta_log_lambda + 0.5).astype(int)

            var_lss = Forest.get_var_lss(forest.log_lambda)
            eta = Forest.get_eta(forest.log_lambda)
            fudge = Forest.get_fudge(forest.log_lambda)

            var_pipe = stack_trans[bins]**2/forest.ivar
            variance = eta * var_pipe + var_lss + fudge / var_pipe
            forest.weights = 1. / variance
    
    
    results.write([log_lambda_rest_frame,
                   Forest.get_mean_cont(log_lambda_rest_frame), mean_cont_weight],
                  names=['LOGLAM_REST', 'MEAN_CONT', 'WEIGHT'],
                  extname='CONT')
    
    
    stack_log_lambda, stack_delta, stack_weight = prep_del.stack(data, stack_from_deltas=True)
    
    results.write([stack_log_lambda, stack_delta, stack_weight],
                  names=['LOGLAM', 'STACK', 'WEIGHT'],
                  header=header,
                  extname='STACK')
    
    var_lss = Forest.get_var_lss(stack_log_lambda)
    eta = Forest.get_eta(stack_log_lambda)
    fudge = Forest.get_fudge(stack_log_lambda) 
    
    results.write([stack_log_lambda, eta, var_lss, fudge],
                  names=['loglam', 'eta', 'var_lss', 'fudge'],
                  extname='WEIGHT')
    
    results.write([np.array(tids_list)],
                names=['THINGID'],
                extname='THINGID_DELTA')
    
    results.close()
        
    # save results
    for index, healpix in enumerate(sorted(data)): ## loop over forests 
        if len(data[healpix]) == 0:
            userprint('No data in {}'.format(healpix))
            continue
        results = fitsio.FITS(out_dir + '/delta-{}'.format(healpix) +'.fits','rw', clobber=True)
        for forest in data[healpix]:

            header = [
                    {'name': 'RA',
                        'value': forest.ra,
                        'comment': 'Right Ascension [rad]'
                    },
                    {'name': 'DEC',
                        'value': forest.dec,
                        'comment': 'Declination [rad]'
                    },
                    {'name': 'Z',
                        'value': forest.z_qso,
                        'comment': 'Redshift'
                    },
                    {'name':'PMF',
                        'value':'{}-{}-{}'.format(forest.plate, forest.mjd,forest.fiberid)
                    },
                    {'name': 'THING_ID',
                        'value': forest.thingid,
                        'comment': 'Object identification'
                    },
                    {'name': 'PLATE',
                        'value': forest.plate
                    },
                    {'name': 'MJD',
                        'value': forest.mjd,
                        'comment': 'Modified Julian date'
                    },
                    {'name': 'FIBERID',
                        'value': forest.fiberid
                    },
                ]

            cols = [forest.log_lambda, forest.delta, forest.weights,forest.cont,forest.mean_trans, forest.continuum]
            names = ['LOGLAM', 'DELTA', 'WEIGHT', 'MEAN_EXP_FLUX','MEAN_TRANS','CONT']
            units = ['log Angstrom', '', '', '','','']

            results.write(cols,
                          names=names,
                          header=header,
                          units=units,
                          extname=str(forest.thingid))

        results.close()
        userprint("\rwrite {} of {}: {} quasars".format(index, len(data),len(data[healpix])),end="")

    userprint("")


