import matplotlib as mpl
mpl.use('Agg')
import numpy as np
import acor
import pylab as pl
from cbamf.cu import nbl, fields
from cbamf import observers, samplers, models, engines, initializers, states
from time import sleep
import itertools
import sys
import pickle

GN = 64
GS = 0.01
PHI = 0.47
RADIUS = 8.0
PSF = (1.6, 2)
ORDER = (3,3,3)

sweeps = 20
samples = 10
burn = sweeps - samples

PAD = int(2*RADIUS)
SIZE = int(3*RADIUS)
TPAD = PAD+SIZE/2

def renorm(s, doprint=1):
    p, r = s.state[s.b_pos], s.state[s.b_rad]
    nbl.naive_renormalize_radii(p, r, 1)
    s.state[s.b_pos], s.state[s.b_rad] = p, r

#pickle.dump([itrue, xstart, rstart, pstart], open("/media/scratch/bamf_ic.pkl", 'w'))
#itrue, xstart, rstart, pstart = pickle.load(open("/media/scratch/bamf_ic.pkl"))
ipure, itrue, xstart, rstart, pstart = initializers.fake_image_3d(GN, phi=PHI, noise=GS, radius=RADIUS, psf=PSF)
ipure = np.pad(ipure, TPAD, mode='constant', constant_values=-10)
itrue = np.pad(itrue, TPAD, mode='constant', constant_values=-10)
xstart = xstart + TPAD

bkgpoly = np.zeros(np.prod(ORDER))
strue = np.hstack([xstart.flatten(), rstart, pstart, bkgpoly.ravel(), np.zeros(1)]).copy()
s = states.FlourescentParticlesWithBkgCUDA(GN, itrue, pad=2*PAD, state=strue.copy().astype('float64'), order=ORDER)
renorm(s)

np.random.seed(10)

def sample_state(image, st, blocks, slicing=True, N=1, doprint=False):
    m = models.PositionsRadiiPSF(image, imsig=GS)

    eng = engines.SequentialBlockEngine(m, st)
    opsay = observers.Printer()
    ohist = observers.HistogramObserver(block=blocks[0])
    eng.add_samplers([samplers.SliceSampler(RADIUS/1e1, block=b) for b in blocks])

    eng.add_likelihood_observers(opsay) if doprint else None
    eng.add_state_observers(ohist)

    eng.dosteps(N)
    m.free()
    return ohist

def sample_ll(image, st, element, size=0.1, N=1000):
    m = models.PositionsRadiiPSF(image, imsig=GS)
    start = st.state[element]

    ll = []
    vals = np.linspace(start-size, start+size, N)
    for val in vals:
        st.update(element, val)
        l = m.loglikelihood(st)
        ll.append(l)
    m.free()
    return vals, np.array(ll)

def scan_noise(image, st, element, size=0.01, N=1000):
    start = st.state[element]

    xs, ys = [], []
    for i in xrange(N):
        print i
        test = image + np.random.normal(0, GS, image.shape)
        x,y = sample_ll(test, st, element, size=size, N=300)
        st.update(element, start)
        xs.append(x)
        ys.append(y)

    return xs, ys

#"""
#raise IOError
import time
if True:
    h = []
    for i in xrange(sweeps):
        print '{:=^79}'.format(' Sweep '+str(i)+' ')

        print '{:-^39}'.format(' POS / RAD ')
        a0 = time.time()
        for particle in xrange(s.N):
            print particle
            sys.stdout.flush()

            renorm(s)

            s.set_current_particle(particle, max_size=SIZE)
            blocks = s.blocks_particle()
            sample_state(itrue, s, blocks)
        b0 = time.time()
        print b0-a0
        print '{:-^39}'.format(' PSF ')
        s.set_current_particle(max_size=SIZE)
        blocks = s.explode(s.create_block('psf'))
        sample_state(itrue, s, blocks)

        print '{:-^39}'.format(' BKG ')
        s.set_current_particle(max_size=SIZE)
        blocks = (s.create_block('bkg'),)
        sample_state(itrue, s, blocks)

        if i > burn:
            h.append(s.state.copy())

    h = np.array(h)
    #return h

#h = cycle(itrue, xstart, rstart, pstart, sweeps, sweeps-samples, size=SIZE)
mu = h.mean(axis=0)
std = h.std(axis=0)
pl.figure(figsize=(20,4))
pl.errorbar(xrange(len(mu)), (mu-strue), yerr=5*std/np.sqrt(samples),
        fmt='.', lw=0.15, alpha=0.5)
pl.vlines([0,3*GN-0.5, 4*GN-0.5, 4*GN+s.psfn], -1, 1, linestyle='dashed', lw=4, alpha=0.5)
pl.hlines(0, 0, len(mu), linestyle='dashed', lw=5, alpha=0.5)
pl.xlim(0, len(mu))
pl.ylim(-0.02, 0.02)
pl.show()
#"""
