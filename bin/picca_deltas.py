#!/usr/bin/env python

from __future__ import print_function

import sys
import os
import fitsio
import numpy as np
import scipy as sp
from scipy.interpolate import interp1d
from multiprocessing import Pool
from math import isnan
import argparse

from picca.data import forest, delta
from picca import prep_del, io, constants
from picca.utils import print
from picca import constants


def cont_fit(data):
    for d in data:
        d.cont_fit()
    return data


if __name__ == '__main__':

    parser = argparse.ArgumentParser(formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        description='Compute the delta field from a list of spectra')

    parser.add_argument('--out-dir',type=str,default=None,required=True,
        help='Output directory')

    parser.add_argument('--drq', type=str, default=None, required=True,
        help='Catalog of objects in DRQ format')

    parser.add_argument('--in-dir', type=str, default=None, required=True,
        help='Directory to spectra files')

    parser.add_argument('--log',type=str,default='input.log',required=False,
        help='Log input data')

    parser.add_argument('--iter-out-prefix',type=str,default='iter',required=False,
        help='Prefix of the iteration file')

    parser.add_argument('--mode',type=str,default='pix',required=False,
        help='Open mode of the spectra files: pix, spec, spcframe, spplate, desi')

    parser.add_argument('--best-obs',action='store_true', required=False,
        help='If mode == spcframe, then use only the best observation')

    parser.add_argument('--single-exp',action='store_true', required=False,
        help='If mode == spcframe, then use only one of the available exposures. If best-obs then choose it among those contributing to the best obs')

    parser.add_argument('--zqso-min',type=float,default=None,required=False,
        help='Lower limit on quasar redshift from drq')

    parser.add_argument('--zqso-max',type=float,default=None,required=False,
        help='Upper limit on quasar redshift from drq')

    parser.add_argument('--keep-bal',action='store_true',required=False,
        help='Do not reject BALs in drq')

    parser.add_argument('--bi-max',type=float,required=False,default=None,
        help='Maximum CIV balnicity index in drq (overrides --keep-bal)')

    parser.add_argument('--lambda-min',type=float,default=3600.,required=False,
        help='Lower limit on observed wavelength [Angstrom]')

    parser.add_argument('--lambda-max',type=float,default=5500.,required=False,
        help='Upper limit on observed wavelength [Angstrom]')

    parser.add_argument('--lambda-rest-min',type=float,default=1040.,required=False,
        help='Lower limit on rest frame wavelength [Angstrom]')

    parser.add_argument('--lambda-rest-max',type=float,default=1200.,required=False,
        help='Upper limit on rest frame wavelength [Angstrom]')

    parser.add_argument('--rebin',type=int,default=3,required=False,
        help='Rebin wavelength grid by combining this number of adjacent pixels (ivar weight)')

    parser.add_argument('--npix-min',type=int,default=50,required=False,
        help='Minimum of rebined pixels')

    parser.add_argument('--dla-vac',type=str,default=None,required=False,
        help='DLA catalog file')

    parser.add_argument('--dla-mask',type=float,default=0.8,required=False,
        help='Lower limit on the DLA transmission. Transmissions below this number are masked')

    parser.add_argument('--absorber-vac',type=str,default=None,required=False,
        help='Absorber catalog file')

    parser.add_argument('--absorber-mask',type=float,default=2.5,required=False,
        help='Mask width on each side of the absorber central observed wavelength in units of 1e4*dlog10(lambda)')

    parser.add_argument('--mask-file',type=str,default=None,required=False,
        help='Path to file to mask regions in lambda_OBS and lambda_RF. In file each line is: region_name region_min region_max (OBS or RF) [Angstrom]')

    parser.add_argument('--optical-depth', type=str, default=None, required=False,
        help='Correct for the optical depth: tau_1 gamma_1 absorber_1 tau_2 gamma_2 absorber_2 ...', nargs='*')

    parser.add_argument('--dust-map', type=str, default=None, required=False,
        help='Path to DRQ catalog of objects for dust map to apply the Schlegel correction')

    parser.add_argument('--flux-calib',type=str,default=None,required=False,
        help='Path to previously produced picca_delta.py file to correct for multiplicative errors in the pipeline flux calibration')

    parser.add_argument('--ivar-calib',type=str,default=None,required=False,
        help='Path to previously produced picca_delta.py file to correct for multiplicative errors in the pipeline inverse variance calibration')

    parser.add_argument('--eta-min',type=float,default=0.5,required=False,
        help='Lower limit for eta')

    parser.add_argument('--eta-max',type=float,default=1.5,required=False,
        help='Upper limit for eta')

    parser.add_argument('--vlss-min',type=float,default=0.,required=False,
        help='Lower limit for variance LSS')

    parser.add_argument('--vlss-max',type=float,default=0.3,required=False,
        help='Upper limit for variance LSS')

    parser.add_argument('--delta-format',type=str,default=None,required=False,
        help='Format for Pk 1D: Pk1D')

    parser.add_argument('--use-ivar-as-weight', action='store_true', default=False,
        help='Use ivar as weights (implemented as eta = 1, sigma_lss = fudge = 0)')

    parser.add_argument('--use-constant-weight', action='store_true', default=False,
        help='Set all the delta weights to one (implemented as eta = 0, sigma_lss = 1, fudge = 0)')

    parser.add_argument('--order',type=int,default=1,required=False,
        help='Order of the log10(lambda) polynomial for the continuum fit, by default 1.')

    parser.add_argument('--nit',type=int,default=5,required=False,
        help='Number of iterations to determine the mean continuum shape, LSS variances, etc.')

    parser.add_argument('--nproc', type=int, default=None, required=False,
        help='Number of processors')

    parser.add_argument('--nspec', type=int, default=None, required=False,
        help='Maximum number of spectra to read')

    parser.add_argument('--use-resolution-matrix', action='store_true', default = False,
        help='should the resolution matrix be stored with the deltas (only implemented for Pk1D)')

    parser.add_argument('--use-mock-continuum', action='store_true', default = False,
        help='use the mock continuum for computing the deltas')

    parser.add_argument('--linear-binning', action='store_true', default = False,
        help='do all regridding operations on bins in lambda instead of bins in log(lambda)')

    parser.add_argument('--use-desi-P1d-changes', action='store_true', default = False,
        help='use changes put into picca to allow resolution treatment for the P1d more properly with DESI mocks (e.g. different sampling)')
    
    parser.add_argument('--min-SNR', type=float, default = 1,
        help='only use data with at least this SNR, note that MiniSV analyses ran at 0.2, else 1 was default')

    
    parser.add_argument('--mc-rebin-fac', type=int, default = 10,
        help='use pixels coarser by this factor when estimating the mean continuum')

    parser.add_argument('--use-poly-meancont', action='store_true', default = False,
        help='fit a polynomial to the mean continuum instead of doing linear interpolation')

    args = parser.parse_args()

    ## init forest class

    forest.lmin = sp.log10(args.lambda_min)
    forest.lmax = sp.log10(args.lambda_max)
    forest.lmin_rest = sp.log10(args.lambda_rest_min)
    forest.lmax_rest = sp.log10(args.lambda_rest_max)
    forest.mc_rebin_fac = args.mc_rebin_fac
        
    if args.use_desi_P1d_changes:
        args.linear_binning = True
        if args.delta_format == 'Pk1D':
            args.use_resolution_matrix=True
        forest.dlambda = 0.8     #note: desi observations will be half of this in the end...
        #forest.dlambda = 1. #was used for mocks
        forest.dll = None
        forest.linear_binning = True
    elif args.linear_binning:
        forest.dlambda = 1
        forest.dll = None
        forest.linear_binning = True
    else:   
        forest.dll = args.rebin * 1e-4
        forest.dlambda = None

        

    ## minumum dla transmission
    forest.dla_mask = args.dla_mask
    forest.absorber_mask = args.absorber_mask

    ### Find the redshift range
    if (args.zqso_min is None):
        args.zqso_min = max(0.,args.lambda_min/args.lambda_rest_max -1.)
        print(" zqso_min = {}".format(args.zqso_min) )
    if (args.zqso_max is None):
        args.zqso_max = max(0.,args.lambda_max/args.lambda_rest_min -1.)
        print(" zqso_max = {}".format(args.zqso_max) )

    forest.var_lss = interp1d(forest.lmin+np.arange(2)*(forest.lmax-forest.lmin),0.2 + np.zeros(2),fill_value="extrapolate",kind="nearest")
    forest.eta = interp1d(forest.lmin+np.arange(2)*(forest.lmax-forest.lmin), sp.ones(2),fill_value="extrapolate",kind="nearest")
    forest.fudge = interp1d(forest.lmin+np.arange(2)*(forest.lmax-forest.lmin), np.zeros(2),fill_value="extrapolate",kind="nearest")
    forest.mean_cont = interp1d(forest.lmin_rest+np.arange(2)*(forest.lmax_rest-forest.lmin_rest),1+np.zeros(2))

    ### Fix the order of the continuum fit, 0 or 1.
    if args.order:
        if (args.order != 0) and (args.order != 1):
            print("ERROR : invalid value for order, must be eqal to 0 or 1. Here order = %i"%(order))
            sys.exit(12)

### todo: make sure the following is fine with linearly binned pixels
    ### Correct multiplicative pipeline flux calibration
    if (args.flux_calib is not None):
        try:
            vac = fitsio.FITS(args.flux_calib)
            ll_st = vac[1]['loglam'][:]
            st    = vac[1]['stack'][:]
            w     = (st!=0.)
            forest.correc_flux = interp1d(ll_st[w],st[w],fill_value="extrapolate",kind="nearest")
            vac.close()
        except:
            print(" Error while reading flux_calib file {}".format(args.flux_calib))
            sys.exit(1)

    ### Correct multiplicative pipeline inverse variance calibration
    if (args.ivar_calib is not None):
        try:
            vac = fitsio.FITS(args.ivar_calib)
            ll  = vac[2]['LOGLAM'][:]
            eta = vac[2]['ETA'][:]
            forest.correc_ivar = interp1d(ll,eta,fill_value="extrapolate",kind="nearest")
            vac.close()
        except:
            print(" Error while reading ivar_calib file {}".format(args.ivar_calib))
            sys.exit(1)

    ### Apply dust correction
    if not args.dust_map is None:
        print("applying dust correction")
        forest.ebv_map = io.read_dust_map(args.dust_map)

    nit = args.nit

    log = open(os.path.expandvars(args.log),'w')

    data,ndata,healpy_nside,healpy_pix_ordering = io.read_data(os.path.expandvars(args.in_dir), args.drq, args.mode,\
        zmin=args.zqso_min, zmax=args.zqso_max, nspec=args.nspec, log=log,\
        keep_bal=args.keep_bal, bi_max=args.bi_max, order=args.order,\
        best_obs=args.best_obs, single_exp=args.single_exp, pk1d=args.delta_format )

    ### Get the lines to veto
    usr_mask_obs    = None
    usr_mask_RF     = None
    usr_mask_RF_DLA = None
    if (args.mask_file is not None):
        args.mask_file = os.path.expandvars(args.mask_file)
        try:
            usr_mask_obs    = []
            usr_mask_RF     = []
            usr_mask_RF_DLA = []
            with open(args.mask_file, 'r') as f:
                loop = True
                for l in f:
                    if (l[0]=='#'): continue
                    l = l.split()
                    if (l[3]=='OBS'):
                        usr_mask_obs    += [ [float(l[1]),float(l[2])] ]
                    elif (l[3]=='RF'):
                        usr_mask_RF     += [ [float(l[1]),float(l[2])] ]
                    elif (l[3]=='RF_DLA'):
                        usr_mask_RF_DLA += [ [float(l[1]),float(l[2])] ]
                    else:
                        raise
            usr_mask_obs    = sp.log10(sp.asarray(usr_mask_obs))
            usr_mask_RF     = sp.log10(sp.asarray(usr_mask_RF))
            usr_mask_RF_DLA = sp.log10(sp.asarray(usr_mask_RF_DLA))
            if usr_mask_RF_DLA.size==0:
                usr_mask_RF_DLA = None

        except:
            print(" Error while reading mask_file file {}".format(args.mask_file))
            sys.exit(1)

    ### Veto lines
    if not usr_mask_obs is None:
        if ( usr_mask_obs.size+usr_mask_RF.size!=0):              
            for p in data:
                for d in data[p]:
                    d.mask(mask_obs=usr_mask_obs , mask_RF=usr_mask_RF)

    ### Veto absorbers
    if not args.absorber_vac is None:
        print("INFO: Adding absorbers")
        absorbers = io.read_absorbers(args.absorber_vac)
        nb_absorbers_in_forest = 0
        for p in data:
            for d in data[p]:
                if d.thid in absorbers:
                    for lambda_absorber in absorbers[d.thid]:
                        d.add_absorber(lambda_absorber)
                        nb_absorbers_in_forest += 1
        log.write("Found {} absorbers in forests\n".format(nb_absorbers_in_forest))

    ### Apply optical depth
    if not args.optical_depth is None:
        print("INFO: Adding {} optical depths".format(len(args.optical_depth)//3))
        assert len(args.optical_depth)%3==0
        for idxop in range(len(args.optical_depth)//3):
            tau = float(args.optical_depth[3*idxop])
            gamma = float(args.optical_depth[3*idxop+1])
            waveRF = constants.absorber_IGM[args.optical_depth[3*idxop+2]]
            print("INFO: Adding optical depth for tau = {}, gamma = {}, waveRF = {} A".format(tau,gamma,waveRF))
            for p in data:
                for d in data[p]:
                    d.add_optical_depth(tau,gamma,waveRF)

    ### Correct for DLAs
    if not args.dla_vac is None:
        print("INFO: Adding DLAs")
        sp.random.seed(0)
        dlas = io.read_dlas(args.dla_vac)
        nb_dla_in_forest = 0
        
        for p in data:
            for d in data[p]:
                if d.thid in dlas:
                    for dla in dlas[d.thid]:
                        d.add_dla(dla[0],dla[1],usr_mask_RF_DLA)
                        nb_dla_in_forest += 1
        log.write("Found {} DLAs in forests\n".format(nb_dla_in_forest))

    ## cuts
    log.write("INFO: Input sample has {} forests\n".format(sp.sum([len(p) for p in data.values()])))
    lstKeysToDel = []
    for p in data.keys():
        l = []
        for d in data[p]:
            if not hasattr(d,'ll') or len(d.ll) < args.npix_min:
                log.write("INFO: Rejected {} due to forest too short\n".format(d.thid))
                continue

            if isnan((d.fl*d.iv).sum()):
                log.write("INFO: Rejected {} due to nan found\n".format(d.thid))
                continue

            if(args.use_constant_weight and (d.fl.mean()<=0.0 or d.mean_SNR<=args.min_SNR )):
                log.write("INFO: Rejected {} due to negative mean or too low SNR found\n".format(d.thid))
                continue

            l.append(d)
            log.write("{} {}-{}-{} accepted\n".format(d.thid,
                d.plate,d.mjd,d.fid))

        data[p][:] = l
        if len(data[p])==0:
            lstKeysToDel += [p]

    for p in lstKeysToDel:
        del data[p]

    log.write("INFO: Remaining sample has {} forests\n".format(sp.sum([len(p) for p in data.values()])))

    for p in data:
        for d in data[p]:
            assert hasattr(d,'ll')
                
    for it in range(nit):
        print("iteration: ", it)
        nfit = 0
        sort = sp.array(list(data.keys())).argsort()
        
        if args.nproc>1:
            pool = Pool(processes=args.nproc)
            data_fit_cont = pool.map(cont_fit, sp.array(list(data.values()))[sort] )
        else:
            data_fit_cont=[]
            for i in sp.array(list(data.values()))[sort]:
                data_fit_cont.append(cont_fit(i))
        for i, p in enumerate(sorted(list(data.keys()))):
            data[p] = data_fit_cont[i]

        print("done")
        if args.nproc>1:
            pool.close()

        if it < nit-1:
            
            ll_rest, mc, wmc = prep_del.mc(data)
            # shouldn't the mean continuum be obtained using a fitting funciton to mc instead of using linear interpolation ??? 
            # This would allow way smoother continua in this step if there's few spectra, this fit could potentially also be done to the non-stacked data
            if args.use_poly_meancont:
                import iminuit
                ll_cen=np.mean(ll_rest)
                fit_order=12
                def fcn(pars):
                  poly=np.polyval(pars,ll_rest[wmc>0]-ll_cen)
                  return 0.5*np.sum(((mc[wmc>0] - poly)/np.std(mc[wmc>0]))**2)

                if it==0:
                    fitter=iminuit.Minuit.from_array_func(fcn,np.zeros(fit_order+1), error=np.ones(fit_order+1),errordef=0.5)
                    fitter.values['x{:d}'.format(fit_order)]=np.mean(mc[wmc>0])    #probably need to fix order in here or sth
                if it>0:
                    #this is for initializing at previous best fit, but using the new lambda/mc arrays
                    oldpars=fitter.np_values()
                    fitter=iminuit.Minuit.from_array_func(fcn,oldpars, error=oldpars/2,errordef=0.5)
                    #for i in range(args.order):
                    #    fitter.fixed['x{:d}'.format(fit_order-i)]=True
                    #maybe need to fix some pars in that case

                fmin,_=fitter.migrad()
                if not fmin.is_valid:
                    #raise ValueError("Error in fitting mean cont with polynomial")
                    pass
                    #seems to get invalid fits sometimes, but still doesn't necessarily look terrible
                    #breakpoint()
                def mean_cont_fct(ll):
                    return np.polyval(fitter.np_values(),ll-ll_cen)
                forest.mean_cont = mean_cont_fct
            else:
                forest.mean_cont = interp1d(ll_rest[wmc>0.], forest.mean_cont(ll_rest[wmc>0.]) * mc[wmc>0.], fill_value = "extrapolate")
            if not (args.use_ivar_as_weight or args.use_constant_weight):
                ll, eta, vlss, fudge, nb_pixels, var, var_del, var2_del,\
                    count, nqsos, chi2, err_eta, err_vlss, err_fudge = \
                        prep_del.var_lss(data,(args.eta_min,args.eta_max),(args.vlss_min,args.vlss_max))
                forest.eta = interp1d(ll[nb_pixels>0], eta[nb_pixels>0],
                    fill_value = "extrapolate",kind="nearest")
                forest.var_lss = interp1d(ll[nb_pixels>0], vlss[nb_pixels>0.],
                    fill_value = "extrapolate",kind="nearest")
                forest.fudge = interp1d(ll[nb_pixels>0],fudge[nb_pixels>0],
                    fill_value = "extrapolate",kind="nearest")
            else:

                nlss=10 # this value is arbitrary
                ll = forest.lmin + (np.arange(nlss)+.5)*(forest.lmax-forest.lmin)/nlss

                if args.use_ivar_as_weight:
                    print('INFO: using ivar as weights, skipping eta, var_lss, fudge fits')
                    eta = sp.ones(nlss)
                    vlss = np.zeros(nlss)
                    fudge = np.zeros(nlss)
                else :
                    print('INFO: using constant weights, skipping eta, var_lss, fudge fits')
                    eta = np.zeros(nlss)
                    vlss = sp.ones(nlss)
                    fudge=np.zeros(nlss)

                err_eta = np.zeros(nlss)
                err_vlss = np.zeros(nlss)
                err_fudge = np.zeros(nlss)
                chi2 = np.zeros(nlss)

                nb_pixels = np.zeros(nlss)
                var = np.zeros(nlss)
                var_del = np.zeros((nlss, nlss))
                var2_del = np.zeros((nlss, nlss))
                count = np.zeros((nlss, nlss))
                nqsos=np.zeros((nlss, nlss))

                forest.eta = interp1d(ll, eta, fill_value='extrapolate', kind='nearest')
                forest.var_lss = interp1d(ll, vlss, fill_value='extrapolate', kind='nearest')
                forest.fudge = interp1d(ll, fudge, fill_value='extrapolate', kind='nearest')

    
        ll_st,st,wst = prep_del.stack(data)

        ### Save iter_out_prefix
        res = fitsio.FITS(args.iter_out_prefix+"_{:d}.fits.gz".format(it),'rw',clobber=True)
        hd = {}
        hd["NSIDE"] = healpy_nside
        hd["PIXORDER"] = healpy_pix_ordering
        hd["FITORDER"] = args.order
        res.write([ll_st,st,wst],names=['loglam','stack','weight'],header=hd,extname='STACK')
        res.write([ll,eta,vlss,fudge,nb_pixels],names=['loglam','eta','var_lss','fudge','nb_pixels'],extname='WEIGHT')
        res.write([ll_rest,forest.mean_cont(ll_rest),wmc],names=['loglam_rest','mean_cont','weight'],extname='CONT')
        var_out = sp.broadcast_to(var.reshape(1,-1),var_del.shape)
        res.write([var_out,var_del,var2_del,count,nqsos,chi2],names=['var_pipe','var_del','var2_del','count','nqsos','chi2'],extname='VAR')
        res.close()

    ### Save delta
    st = interp1d(ll_st[wst>0.],st[wst>0.],kind="nearest",fill_value="extrapolate")
    deltas = {}
    data_bad_cont = []
        
                
    for p in sorted(data.keys()):
        deltas[p] = [delta.from_forest(d,st,forest.var_lss,forest.eta,forest.fudge, args.use_mock_continuum) for d in data[p] if d.bad_cont is None]
        data_bad_cont = data_bad_cont + [d for d in data[p] if d.bad_cont is not None]


    for d in data_bad_cont:
        log.write("INFO: Rejected {} due to {}\n".format(d.thid,d.bad_cont))

    log.write("INFO: Accepted sample has {} forests\n".format(sp.sum([len(p) for p in deltas.values()])))

    log.close()

    ###
    for p in sorted(deltas.keys()):

        if len(deltas[p])==0: continue
        if (args.delta_format=='Pk1D_ascii') :
            out_ascii = open(args.out_dir+"/delta-{}".format(p)+".txt",'w')
            for d in deltas[p]:
                nbpixel = len(d.de)
                dll = d.dll
                if (args.mode is not None and 'desi' in args.mode) :
                    desi_pixsize=0.8 #set desi pixel size to one angstrom, generalize later
                    #dll = (d.ll[-1]-d.ll[0])/float(len(d.ll)-1)  #this is not the right number given that pixelization is changed at spectra readin
                    dll=sp.median(sp.diff(d.ll)) #this is better as masking is ignored 
                    dll_resmat=sp.median(10**-d.ll)*desi_pixsize/sp.log(10.) #this is 1 angstrom pixel size * mean(1/lambda) or median(1/lambda)
                    d.mean_reso*=constants.speed_light/1000.*dll_resmat*sp.log(10.0)

                line = '{} {} {} '.format(d.plate,d.mjd,d.fid)
                line += '{} {} {} '.format(d.ra,d.dec,d.zqso)
                line += '{} {} {} {} {} '.format(d.mean_z,d.mean_SNR,d.mean_reso,dll,nbpixel)
                for i in range(nbpixel): line += '{} '.format(d.de[i])
                for i in range(nbpixel): line += '{} '.format(d.ll[i])
                for i in range(nbpixel): line += '{} '.format(d.iv[i])
                for i in range(nbpixel): line += '{} '.format(d.diff[i])
                if args.use_resolution_matrix:
                    print('the resolution matrix will only be output when using FITS format')
                line +=' \n'
                out_ascii.write(line)

            out_ascii.close()

        else :
            out = fitsio.FITS(args.out_dir+"/delta-{}".format(p)+".fits.gz",'rw',clobber=True)
            for d in deltas[p]:
                hd = [ {'name':'RA','value':d.ra,'comment':'Right Ascension [rad]'},
                       {'name':'DEC','value':d.dec,'comment':'Declination [rad]'},
                       {'name':'Z','value':d.zqso,'comment':'Redshift'},
                       {'name':'PMF','value':'{}-{}-{}'.format(d.plate,d.mjd,d.fid)},
                       {'name':'THING_ID','value':d.thid,'comment':'Object identification'},
                       {'name':'PLATE','value':d.plate},
                       {'name':'MJD','value':d.mjd,'comment':'Modified Julian date'},
                       {'name':'FIBERID','value':d.fid},
                       {'name': 'ORDER', 'value': d.order, 'comment': 'Order of the continuum fit'},
                       {'name':'LIN_BIN','value':d.linear_binning,'comment':'Used linear wavelength binning'},
                ]

                if (args.delta_format=='Pk1D'):
                    dll = d.dll
                    desi_pixsize=0.8 #set desi pixel size to one angstrom, generalize later
                    if (args.mode is not None and 'desi' in args.mode):
                        #dll = (d.ll[-1]-d.ll[0])/float(len(d.ll)-1)  #this is not the right number given that pixelization is changed at spectra readin
                        dll = sp.mean(sp.diff(d.ll))  #this is better as masking is ignored 
                        dll_resmat=sp.median(10**-d.ll)*desi_pixsize/sp.log(10.) #this is 1 angstrom pixel size * mean(1/lambda) or median(1/lambda)
                        if args.use_resolution_matrix:
                            d.dll_resmat=dll_resmat 
                        d.mean_reso*=constants.speed_light/1000.*dll_resmat*sp.log(10.0)


                    hd += [{'name':'MEANZ','value':d.mean_z,'comment':'Mean redshift'},
                           {'name':'MEANRESO','value':d.mean_reso,'comment':'Mean resolution'},
                           {'name':'MEANSNR','value':d.mean_SNR,'comment':'Mean SNR'},
                    ]
                    hd += [{'name':'DLL','value':dll,'comment':'Loglam bin size [log Angstrom]'}]
                    if args.use_resolution_matrix:
                        hd += [{'name':'DLL_RES', 'value':dll_resmat, 'comment':'Loglam bin size for resolution matrix'}]
                    if d.linear_binning:
                        hd += [{'name':'DLAMBDA', 'value':d.dlambda, 'comment':'Lambda bin size'}]
                            
                    diff = d.diff
                    if diff is None:
                        diff = d.ll*0
                    if args.use_resolution_matrix and (args.mode is not None and 'desi' in args.mode) :
                        resomat=d.reso_matrix.T

                    cols=[d.ll,d.de,d.iv,diff,d.co]
                    names=['LOGLAM','DELTA','IVAR','DIFF','CONT']
                    units=['log Angstrom','','','','']
                    comments = ['Log lambda','Delta field','Inverse variance','Difference','Continuum']
                    if args.use_resolution_matrix and (args.mode is not None and 'desi' in args.mode) :
                        cols.extend([resomat])
                        names.extend(['RESOMAT'])
                        units.extend(['(pixel)'])
                        comments.extend(['Resolution matrix'])
                else :
                    cols=[d.ll,d.de,d.we,d.co]
                    names=['LOGLAM','DELTA','WEIGHT','CONT']
                    units=['log Angstrom','','','']
                    comments = ['Log lambda','Delta field','Pixel weights','Continuum']

                out.write(cols,names=names,header=hd,comment=comments,units=units,extname=str(d.thid))

            out.close()
