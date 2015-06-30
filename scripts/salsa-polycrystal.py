import matplotlib as mpl
mpl.use('Agg')
import numpy as np
import pylab as pl
from cbamf import states, runner, initializers
from cbamf.comp import objs, psfs, ilms
from cbamf.viz import plots

ORDER = (3,3,2)
sweeps = 30
samples = 20
burn = sweeps - samples

sigma = 0.0141
PSF = (2.402, 5.151)
#PAD, FSIZE, RAD, INVERT, IMSIZE, zstart, zscale = 24, 5, 5.04, True, 512, 0, 1.0717
PAD, FSIZE, RAD, INVERT, IMSIZE, zstart, zscale = 22, 6, 7.3, True, 512, 0, 1.06
raw = initializers.load_tiff("/media/scratch/bamf/salsa-polycrystal/1_non.tif")

feat = initializers.normalize(raw[zstart:,:IMSIZE,:IMSIZE], INVERT)
xstart, proc = initializers.local_max_featuring(feat, FSIZE, FSIZE/3.)
itrue = initializers.normalize(feat, True)
itrue = np.pad(itrue, PAD, mode='constant', constant_values=-10)
xstart += PAD
rstart = RAD*np.ones(xstart.shape[0])
initializers.remove_overlaps(xstart, rstart)

imsize = itrue.shape
obj = objs.SphereCollectionRealSpace(pos=xstart, rad=rstart, shape=imsize)
psf = psfs.AnisotropicGaussian(PSF, shape=imsize)
ilm = ilms.Polynomial3D(order=ORDER, shape=imsize)
s = states.ConfocalImagePython(itrue, obj=obj, psf=psf, ilm=ilm,
        zscale=zscale, pad=16, sigma=sigma)

def sample(s):
    h = []
    for i in xrange(sweeps):
        print '{:=^79}'.format(' Sweep '+str(i)+' ')

        runner.sample_particles(s, stepout=0.1)
        runner.sample_block(s, 'ilm', stepout=0.1)
        runner.sample_block(s, 'off', stepout=0.1)
        runner.sample_block(s, 'psf', stepout=0.1)
        runner.sample_block(s, 'zscale')

        if i > burn:
            h.append(s.state.copy())

    h = np.array(h)
    return h
