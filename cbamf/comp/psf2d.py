"""
ORDER IS Z,X,Y!!!!

It might be really dum to change the support size by 6 when the threshold is
crossed (instead of 2 the naive way). Think about it?

TODO:
5. Center shift for aberrated PSFs
"""

import numpy as np
from multiprocessing import cpu_count
from cbamf.util import Tile, cdd

try:
    import pyfftw
    from pyfftw.builders import fftn, ifftn, fft2, ifft2, rfftn, irfftn, rfft2, irfft2
    hasfftw = True
except ImportError as e:
    print "*WARNING* pyfftw not found, switching to numpy.fft (20x slower)"
    hasfftw = False

    
FFTW_PLAN_FAST = 'FFTW_ESTIMATE'
FFTW_PLAN_NORMAL = 'FFTW_MEASURE'
FFTW_PLAN_SLOW = 'FFTW_PATIENT'


#For writing this --
import os,sys
sys.path.append('F:\\BAMF Featuring measurements\\Numerical Point Spread Functions_from Hell')
# import calculate_psf_func_fastest as psf
import calculate_psf_func_complex as psf

class PSF2D(object):
    """
    """
    def __init__(self, params, shape, error = 1e-3, threads=-1, 
            fftw_planning_level=FFTW_PLAN_NORMAL):
        """
        Inputs: 
            - shape: 3-element list-like of the image shape; [z,x,y]. 
                Really only needed for the image depth. 
            - params: The parameters of the PSF. Subclass-dependent.
            - error: The desired value at which to truncate the PSF. 
        """
        self._params = np.array(params).astype('float')
        self.shape = shape
        self.error = error
        
        self.fftw_planning_level = fftw_planning_level
        self.threads = threads if threads > 0 else cpu_count()
        
        self.tile = Tile( (0,0,0) )
                
        #Setting the PSF
        self.update( self._params )
        self.set_tile(Tile( shape ) )#-- this needs to be a 2D tile!!!!!
        
        return None
        
    def get_params( self ):
        return self._params
        
    def get_support_size( self, _ ):
        return self._support_size
        
    def execute(self, field):
        """
        PSF is a 3D array, the same size as the image (field). Each array
        gets convolved and the result gets summed. 
        """
        
        for_ans = np.zeros( field.shape )
        
        for a in xrange(field.shape[0]):
            for_ans[a] = self._conv_2d(field[a], self._kpsf[a], k2=True)
            
        ans = for_ans.sum(axis=0)
        # return ans
        
        #Temporarily, to get it to play nice with cbamf:
        bigger_ans = np.zeros(field.shape)
        for a in xrange(self.shape[0]):
            bigger_ans[a] = ans
            
        return bigger_ans
        
    def _conv_2d(self, field1, field2, k1=False, k2=True):
        """
        k1, k2: Set to true if field1, field2 (respectively) are already 
            fourier transformed
        """
        f1k = field1 if k1 else self.fft2(field1)
        f2k = field2 if k2 else self.fft2(field2)
        
        return self.ifft2( f1k*f2k ).real
        
    def set_tile( self, tile ):
        """
        """
        #Stuff for updating pyfftw if not set:
        if (self.tile.shape != tile.shape).any():
            self.tile = tile
            self._setup_ffts()
        
        self._kpsf = self._make_kpsf(self.tile.shape)
        
        pass
    
    def update(self, params):
        self._params = params.copy()
        self._support_size = self._calc_support_size(self._params)
        self._update_rvecs()
        self._rpsf = self._calc_psf(self._params)
        
        # #Normalizing -- FIXME because you should only change 1 scale factor
        # for a in xrange(self._rpsf.shape[0]):
            # self._rpsf[a] /= self._rpsf[a].sum()
        self._rpsf /= self._rpsf.sum()
        
    def _make_kpsf(self, tile_shape):
        if np.any( (np.array(self._rpsf.shape[1:]) % 2) == 0):
            raise ValueError("self._rpsf must be odd x odd in shape")
        
        kshape = [self.shape[0]] + list(tile_shape[1:3])
        to_return = np.zeros( kshape, dtype='complex' )
        bigp = np.zeros( tile_shape[1:3] )
        
        sz = (np.array(self._rpsf.shape)-1)/2 #self._rpsf is always odd
        
        for a in xrange(to_return.shape[0]):
            #1. put the real psf into the big array, given that rpsf has 0
            #   at the center
            curp = self._rpsf[a]
            bigp[:sz[1]+1,:sz[2]+1]=curp[sz[1]:,sz[2]:]
            bigp[:sz[1]+1,-sz[2]:] =curp[sz[1]:,:sz[2]]
            bigp[-sz[1]:,:sz[2]+1] =curp[:sz[1],sz[2]:]
            bigp[-sz[1]:,-sz[2]:] = curp[:sz[1],:sz[2]]
            
            #2. fft the real psf
            to_return[a] = self.fft2( bigp )
            
        return to_return
    
    def _calc_support_size(self, params):
    
        #Depends on the model of the PSF and self.error. Subclass-dependent.
        #Returns a LIST
        
        #Maybe this should only give the 2D support size, since there is no
        #optical sectioning for a 2D PSF. 
        
        pass
        
    def _calc_psf(self, params):
        
        #calculate the psf for the parameters. Subclass-dependent. 
        #Returns a NUMPY.ARRAY
        pass
        
    def _update_rvecs(self):
        
        zr,xr,yr = [(self._support_size[a]-1)/2 for a in xrange(3)]
        
        self._zr = zr
        self._xr = xr
        self._yr = yr
        
        self._zpts = np.arange(-zr, zr+.1, 1).astype('float')
        self._xpts = np.arange(-xr, xr+.1, 1).astype('float')
        self._ypts = np.arange(-yr, yr+.1, 1).astype('float')
        
        return
        
    #~~~~~~~~~~~~Start pyfftw stuff    
    def fft2(self, field):
        if hasfftw:
            self._fft2_data[:] = field
            self._fft2.execute()
            return self._fft2.get_output_array().copy()
        else:    
            return np.fft.fft2(field)
        
    def ifft2(self,field):
        if hasfftw:
            self._ifft2_data[:] = field
            self._ifft2.execute()
            v = 1.0/self._ifft2_data.size
            return self._ifft2.get_output_array() * v
        else:    
            return np.fft.ifft2(field)
        
    def _setup_ffts(self):
        
        imshape = self.tile.shape[1:]
        if len(imshape) != 2:
            raise RuntimeError("The tile is not longer 3D but this code is")
        
        if hasfftw:
            self._fft2_data = pyfftw.n_byte_align_empty(imshape, 16, 
                    dtype='complex')
            self._fft2 = fft2(self._fft2_data, overwrite_input=False,
                    planner_effort=self.fftw_planning_level, threads=self.threads)

            self._ifft2_data = pyfftw.n_byte_align_empty(imshape, 16, 
                    dtype='complex')
            self._ifft2 = ifft2(self._ifft2_data, overwrite_input=False,
                    planner_effort=self.fftw_planning_level, threads=self.threads)
                    
    #PYFFTW pickling stuff:
    def __getstate__(self):
        odict = self.__dict__.copy()
        
        #Deleting keys we can build:
        cdd( odict, ['_xr','_yr','_zr'] )
        cdd( odict, ['_xg','_yg','_zg'] )
        cdd( odict, ['_xpts','_ypts','_zpts'] )
        
        cdd( odict, ['_rpsf','_kpsf'] )
        
        cdd(odict, ['_fft2', '_ifft2', '_fft2_data', '_ifft2_data'])
        return odict

    def __setstate__(self, idict):
        self.__dict__.update(idict)
        self.tile = Tile((0,0,0))
        self.update(self._params)
        self.set_tile(Tile(self.shape))

    def __str__(self):
        return self.__repr__()

    def __repr__(self):
        return str(self.__class__.__name__)+" {} ".format(self._params)
    
#######~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~#######
#                          Begin subclassed 2D PSFs
#######~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~#######

class PerfectMonochromaticLens(PSF2D):
    
    """
    params is a 2-element numpy.array of floats:
        params[0]: Alpha, opening angle of the lens
        params[1]: k0, the wavelength of the light used. 
    Wavevectors are in units of 2pi/lambda, lambda in pixels.
    """
    
    def _calc_support_size(self, params):
        
        wid = guess_psf_width(self.error, k=params[1], alpha=params[0], \
                round=True)
        
        return np.array([self.shape[0],wid,wid])
        
        return np.array([self.shape[0],wid,wid]).astype('int')
        
    def _calc_psf(self, params):

        x = self._xpts[self._xr:]
        y = self._ypts[self._yr:]
        z = self._zpts
        
        k = params[1]
        
        this_psf = psf.wrap_and_calc_psf( k*x, k*y, k*z,
            psf.calculate_monochrome_brightfield_psf, alpha=params[0], 
            zint=0., n2n1 = 1. )
        
        return xyz_to_zxy(this_psf)

class IndexMismatchedMonochromaticLens(PSF2D):
    
    """
    params is a 2-element numpy.array of floats:
        params[0]: Alpha, opening angle of the lens
        params[1]: k0, the wavelength of the light used. 
        params[2]: zint, the nominal focal distance of the lens into the sample
        params[3]: n2n1, the ratio of index mismatch between the sample & lens
        
    Wavevectors are in units of 2pi/lambda, lambda in pixels.
    """
    
    def _calc_support_size(self, params):

        wid = guess_psf_width(self.error, k=params[1], alpha=params[0], \
                round=True)
        
        return np.array([self.shape[0],wid,wid])

    
    def _calc_psf(self, params):

        x = self._xpts[self._xr:]
        y = self._ypts[self._yr:]
        z = self._zpts
        
        k = params[1]
        
        this_psf = psf.wrap_and_calc_psf( k*x, k*y, k*z,
            psf.calculate_monochrome_brightfield_psf, alpha=params[0], 
            zint=params[2], n2n1 = params[3] )
        
        return xyz_to_zxy(this_psf)        
        
class PerfectPolychromaticLens(PSF2D):
    
    """
    params is a 3-element numpy.array of floats:
        params[0]: Alpha, opening angle of the lens
        params[1]: k0, the mean wavelength of the light. 
        params[2]: sigk, the Gaussian standard-deviation of the light. 
    Wavevectors are in units of 2pi/lambda, lambda in pixels. 
    """
    
    def _calc_support_size(self, params):

        wid = guess_psf_width(self.error, k=params[1], alpha=params[0], \
                round=True)
        
        return np.array([self.shape[0],wid,wid])
        
    def _calc_psf(self, params):
        
        x = self._xpts[self._xr:]
        y = self._ypts[self._yr:]
        z = self._zpts
        
        this_psf = psf.wrap_and_calc_psf( x, y, z,
            psf.calculate_polychrome_brightfield_psf, k0=params[1], 
            sigk=params[2], alpha=params[0], zint=0., n2n1 = 1. )
        
        return xyz_to_zxy(this_psf)
    
class IndexMismatchedPolychromaticLens(PSF2D):
    
    """
    params is a 3-element numpy.array of floats:
        params[0]: Alpha, opening angle of the lens
        params[1]: k0, the mean wavelength of the light. 
        params[2]: sigk, the Gaussian standard-deviation of the light. 
        params[3]: zint, the nominal focal distance of the lens into the sample
        params[4]: n2n1, the ratio of index mismatch between the sample & lens
    Wavevectors are in units of 2pi/lambda, lambda in pixels. 
    """
    
    def _calc_support_size(self, params):

        wid = guess_psf_width(self.error, k=params[1], alpha=params[0], \
                round=True)
        
        return np.array([self.shape[0],wid,wid])

        
    def _calc_psf(self, params):
    
        x = self._xpts[self._xr:]
        y = self._ypts[self._yr:]
        z = self._zpts
        
        this_psf = psf.wrap_and_calc_psf(x, y, z,
            psf.calculate_polychrome_brightfield_psf, k0=params[1], 
            sigk=params[2], alpha=params[0], zint=params[3], n2n1 = params[4])
        
        return xyz_to_zxy(this_psf)  

#debugging PSFS:

class ZProjectionPSF(PSF2D):
    def _calc_support_size(self, params):
        try:
            wid = guess_psf_width(self.error,k=params[1],alpha=params[0],round=True)
            return np.array([self.shape[0],wid,wid])
        except:
            return self.shape
            
    def _calc_psf(self, params):
        
        xg,yg,zg = np.meshgrid(self._xpts,self._ypts, self._zpts, indexing='ij')
        
        x0 = xg == 0
        y0 = yg == 0
        # z0 = self._zpts == 0
        
        center = (x0 & y0)
        
        this_psf = np.zeros( xg.shape )
        this_psf[center] = 1.0
        return xyz_to_zxy(this_psf)
#######~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~#######
#                          End subclassed 2D PSFs
#######~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~#######

        
def xyz_to_zxy( ar ):
    return np.rollaxis(ar,1).T.copy()
    
def zxy_to_xyz( ar ):
    return np.rollaxis(ar.T,1).copy()
    
def guess_psf_width(error, k=1., alpha = 1., round = True):
    """
    Guesses the PSF width, in units of sin(alpha)/k, by assuming that the
    axial PSF is (J1(x)/x)^2
    """
    
    #1. Get the value at which max of the PSF is < error
    first_guess = (8/np.pi / error)**(1./3.)
    
    #2. Move up to find the closest zero
    n = np.ceil(first_guess/np.pi - 0.25)
    second_guess = (n+0.25)*np.pi
    
    to_return = second_guess / k / np.sin(alpha)
    
    if round:
        return int(np.round(to_return))*2+1
    else:
        return to_return*2+1
        
def guess_aberration_shift(alpha, zint, n2n1):
    """
    Returns a guess at the center of the PSF for a set of lens parameters. 
    Inputs: 
        - alpha: Float, opening angle of lens. 
        - n2n1:  Float, ratio of index mismatched
        - zint:  Float, focusing postion of the lens. 
    Comments:
        Uses a geometric optics relation for <z>, based on the average 
            position the rays intersect the optical axis. 
        Works in the limit that the index mismatch is small (dropping 3rd 
            order terms). 
    """
    
    eps = 1-n2n1
    
    ca = np.cos(alpha)
    sa = 1.0/ca
    
    order_1 = -1 - (ca+sa-1)/(1-ca)
    order_2 = -1./6.*(8-3*ca-6*sa)/(1-ca) + 0.5*(ca+sa-2)/(1-ca) + 1
    
    slope = eps*order_1 + eps*eps*order_2
    
    return slope * zint
    
    
from scipy.optimize import minimize_scalar
def calc_aberration_shift(alpha, zint, n2n1, func=psf.calculate_monochrome_brightfield_psf,\
        **kwargs):
    """
    Calculates the exact shift of the PSF by looking for max(psf(x=0,y=0,z))
    """
    
    #First a guess:
    mx_gs = guess_aberration_shift(alpha, zint, n2n1)
    #Then a generous bracket:
    bracket = np.sort( [-2*mx_gs, 4*mx_gs] )
    
    for_min = lambda z: -1*func( np.array([0]),np.array([0]),np.array([z]).ravel(), \
        alpha=alpha,zint=zint,n2n1=n2n1,**kwargs)
    
    ans = minimize_scalar( for_min, bracket )
    
    return ans.x    