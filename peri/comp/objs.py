import numpy as np
from scipy.special import erf
from scipy.weave import inline
from scipy.linalg import expm3

from peri.comp import Component
from peri.util import Tile, cdd, amin, amax, listify, delistify

# maximum number of iterations to get an exact volume
MAX_VOLUME_ITERATIONS = 10

#=============================================================================
# Forms of the platonic sphere interpolation function
#=============================================================================
def norm(a):
    return np.sqrt((a**2).sum(axis=-1))

def inner(r, p, a, zscale=1.0):
    eps = np.array([1,1,1])*1e-8
    s = np.array([zscale, 1.0, 1.0])

    d = (r-p-eps)*s
    n = norm(d)
    dhat = d / n[...,None]

    o = norm((d - a*dhat)/s)
    return o * np.sign(n - a)

def sphere_bool(dr, a, alpha):
    return 1.0*(dr < 0)

def sphere_lerp(dr, a, alpha):
    """ Linearly interpolate the pixels for the platonic object """
    return (1-np.clip((dr+alpha) / (2*alpha), 0, 1))

def sphere_logistic(dr, a, alpha):
    """ Classic logistic interpolation """
    return 1.0/(1.0 + np.exp(alpha*dr))

def sphere_triangle_cdf(dr, a, alpha):
    """ Cumulative distribution function for the traingle distribution """
    p0 = (dr+alpha)**2/(2*alpha**2)*(0 > dr)*(dr>-alpha)
    p1 = 1*(dr>0)-(alpha-dr)**2/(2*alpha**2)*(0<dr)*(dr<alpha)
    return (1-np.clip(p0+p1, 0, 1))


def sphere_analytical_gaussian(dr, a, alpha=0.2765):
    """
    Analytically calculate the sphere's functional form by convolving the
    Heavyside function with first order approximation to the sinc, a Gaussian.
    The alpha parameters controls the width of the approximation -- should be
    1, but is fit to be roughly 0.2765
    """
    term1 = 0.5*(erf((dr+2*a)/(alpha*np.sqrt(2))) + erf(-dr/(alpha*np.sqrt(2))))
    term2 = np.sqrt(0.5/np.pi)*(alpha/(dr+a+1e-10)) * (
                np.exp(-0.5*dr**2/alpha**2) - np.exp(-0.5*(dr+2*a)**2/alpha**2)
            )
    return term1 - term2

def sphere_analytical_gaussian_trim(dr, a, alpha=0.2765, cut=1.6):
    """
    See sphere_analytical_gaussian_exact.

    I trimmed to terms from the functional form that are essentially zero (1e-8)
    for r0 > cut (~1.5), a fine approximation for these platonic anyway.
    """
    m = np.abs(dr) <= cut

    # only compute on the relevant scales
    rr = dr[m]
    t = -rr/(alpha*np.sqrt(2))
    q = 0.5*(1 + erf(t)) - np.sqrt(0.5/np.pi)*(alpha/(rr+a+1e-10)) * np.exp(-t*t)

    # fill in the grid, inside the interpolation and outside where values are constant
    ans = 0*dr
    ans[m] = q
    ans[dr >  cut] = 0
    ans[dr < -cut] = 1
    return ans

def sphere_analytical_gaussian_fast(dr, a, alpha=0.2765, cut=1.20):
    """
    See sphere_analytical_gaussian_trim, but implemented in C with
    fast erf and exp approximations found at
        Abramowitz and Stegun: Handbook of Mathematical Functions
        A Fast, Compact Approximation of the Exponential Function

    The default cut 1.25 was chosen based on the accuracy of fast_erf
    """

    functions = """
    double fast_erf(double x){
        double sgn = 1.0;

        if (x < 0){
            sgn = -1.0;
            x = -x;
        }

        double p = 0.47047;
        double a1 =  0.3480242;
        double a2 = -0.0958798;
        double a3 =  0.7478556;
        double t1 = 1.0/(1 + p*x);
        double t2 = t1*t1;
        double t3 = t1*t2;
        return sgn*(1 - (a1*t1 + a2*t2 + a3*t3)*exp(-x*x));
    }

    static union
    {
        double d;
        struct
        {
            #ifdef LITTLE_ENDIAN
                int j, i;
            #else
                int i, j;
            #endif
        } n;
    } _eco;

    #define EXP_A (1048576 /M_LN2)
    #define EXP_C 60801
    #define fast_exp(y) (_eco.n.i = EXP_A*(y) + (1072693248 - EXP_C), _eco.d)
    """

    code = """
    double coeff1 = 1.0/(alpha*sqrt(2.0));
    double coeff2 = sqrt(0.5/pi)*alpha;

    for (int i=0; i<N; i++){
        double dri = dr[i];
        if (dri < cut && dri > -cut){
            double t = -dri*coeff1;
            ans[i] = 0.5*(1+fast_erf(t)) - coeff2/(dri+a+1e-10) * fast_exp(-t*t);
        } else {
            ans[i] = 0.0*(dri > cut) + 1.0*(dri < -cut);
        }
    }
    """

    shape = r.shape
    r = r.flatten()
    N = self.N
    ans = r*0
    pi = np.pi

    inline(code, arg_names=['dr', 'a', 'alpha', 'cut', 'ans', 'pi', 'N'],
            support_code=functions, verbose=0)
    return ans.reshape(shape)

def sphere_constrained_cubic(dr, a, alpha):
    """
    Sphere generated by a cubic interpolant constrained to be (1,0) on
    (r0-sqrt(3)/2, r0+sqrt(3)/2), the size of the cube in the (111) direction.
    """
    sqrt3 = np.sqrt(3)

    b_coeff = a*0.5/sqrt3*(1 - 0.6*sqrt3*alpha)/(0.15 + a*a)
    rscl = np.clip(dr, -0.5*sqrt3, 0.5*sqrt3)

    a, d = rscl + 0.5*sqrt3, rscl - 0.5*sqrt3
    return alpha*d*a*rscl + b_coeff*d*a - d/sqrt3

try:
    sphere_analytical_gaussian_fast(np.linspace(0,10,10), 5.0)
except Exception as e:
    sphere_analytical_gaussian_fast = sphere_analytical_gaussian_trim

def exact_volume_sphere(rvec, pos, radius, zscale=1.0, volume_error=1e-5,
        function=sphere_analytical_gaussian, max_radius_change=1e-2, args=()):
    """
    Perform an iterative method to calculate the effective sphere that perfectly
    (up to the volume_error) conserves volume.  Return the resulting image
    """
    vol_goal = 4./3*np.pi*radius**3 / zscale
    rprime = radius

    dr = inner(rvec, pos, rprime, zscale=zscale)
    t = function(dr, rprime, *args)
    for i in xrange(MAX_VOLUME_ITERATIONS):
        vol_curr = np.abs(t.sum())
        if np.abs(vol_goal - vol_curr)/vol_goal < volume_error:
            break

        rprime = rprime + 1.0*(vol_goal - vol_curr) / (4*np.pi*rprime**2)

        if np.abs(rprime - radius)/radius > max_radius_change:
            break

        dr = inner(rvec, pos, rprime, zscale=zscale)
        t = function(dr, rprime, *args)

    return t

#=============================================================================
# Actual sphere collection (and slab)
#=============================================================================
class PlatonicSpheresCollection(Component):
    category = 'obj'

    def __init__(self, pos, rad, shape=None, zscale=1.0, support_pad=2,
            method='exact-gaussian-fast', alpha=None, user_method=None,
            exact_volume=True, volume_error=1e-5, max_radius_change=1e-2,
            param_prefix='sph'):
        """
        A collection of spheres in real-space with positions and radii, drawn
        not necessarily on a uniform grid (i.e. scale factor associated with
        z-direction).  There are many ways to draw the sphere, currently
        supported  methods can be one of:
            [
                'bool', 'lerp', 'logistic', 'triangle', 'constrained-cubic',
                'exact-gaussian', 'exact-gaussian-trim', 'exact-gaussian-fast',
                'user-method'
            ]

        Parameters:
        -----------
        pos : ndarray [N,3]
            Initial positions of the spheres

        rad : ndarray [N]
            Initial radii of the spheres

        shape : tuple
            Shape of the field over which to draw the platonic spheres

        zscale : float
            scaling of z-pixels in the platonic image

        support_pad : int
            how much to pad the boundary of particles when calculating
            support so that there is not more contribution

        method : string
            The sphere drawing function to use, see above.

        alpha : float
            Parameter supplied to sphere drawing function, set to value to
            override default value

        user_method : tuple (function, parameters)
            Provide your own sphere function to the drawing method. First
            element of tuple is function with call signature func(dr, a, *args)
            where the second element is the *args that are not the distance
            to edge (dr) or particles radius (a). `method` must be set to
            'user-method'.

        exact_volume : boolean
            whether to iterate effective particle size until exact volume
            (within volume_error) is achieved

        volume_error : float
            relative volume error tolerance in iteration steps

        max_radius_change : float
            maximum relative radius change allowed during iteration (due to
            edge particles and other confounding factors)
        """
        self.support_pad = support_pad
        self.pos = pos.astype('float')
        self.rad = rad.astype('float')
        self.zscale = zscale
        self.exact_volume = exact_volume
        self.volume_error = volume_error
        self.max_radius_change = max_radius_change
        self.user_method = user_method
        self.param_prefix = param_prefix

        self.set_draw_method(method=method, alpha=alpha, user_method=user_method)

        self.shape = shape
        self.setup_variables()
        if self.shape:
            self.initialize()

    def initialize(self):
        """ Start from scratch and initialize all objects """
        self.rvecs = self.shape.coords(form='vector')
        self.particles = np.zeros(self.shape.shape)

        for i, (p0, r0) in enumerate(zip(self.pos, self.rad)):
            self._draw_particle(p0, r0)

    def setup_variables(self):
        self._params = []
        for i, (p0, r0) in enumerate(zip(self.pos, self.rad)):
            self._params.extend([self._i2p(i, c) for c in ['x','y','z','a']])
        self._params += ['zscale']

    def update(self, params, values):
        """
        Update the platonic image of spheres given new parameter values
        """
        # figure out which particles are going to be updated, or if the
        # zscale needs to be updated
        dozscale, particles = self._update_type(params)

        # if we are updating the zscale, everything must change, so just start
        # fresh will be faster instead of add subtract
        if dozscale:
            self.set_values(params, values)
            self.initialize()
            return

        # otherwise, update individual particles. delete the current versions
        # of the particles update the particles, and redraw them anew at the
        # places given by (params, values)
        for n in particles:
            self._draw_particle(self.pos[n], self.rad[n], -1)

        self.set_values(params, values)

        for n in particles:
            self._draw_particle(self.pos[n], self.rad[n], +1)

    def get_values(self, params):
        values = []
        for p in listify(params):
            typ, ind = self._p2i(p)
            if typ == 'zscale':
                values.append(self.zscale)
            elif typ == 'x':
                values.append(self.pos[ind][2])
            elif typ == 'y':
                values.append(self.pos[ind][1])
            elif typ == 'z':
                values.append(self.pos[ind][0])
            elif typ == 'a':
                values.append(self.rad[ind])
        return delistify(values)

    def set_values(self, params, values):
        for p,v in zip(listify(params), listify(values)):
            typ, ind = self._p2i(p)
            if typ == 'zscale':
                self.zscale = v
            elif typ == 'x':
                self.pos[ind][2] = v
            elif typ == 'y':
                self.pos[ind][1] = v
            elif typ == 'z':
                self.pos[ind][0] = v
            elif typ == 'a':
                self.rad[ind] = v

    @property
    def params(self):
        return self._params

    @property
    def values(self):
        return self.get_values(self._params)

    def get_update_tile(self, params, values):
        """ Get the amount of support size required for a particular update. """
        dozscale, particles = self._update_type(params)

        # if we are updating the zscale then really everything should change
        if dozscale:
            return self.shape.copy()

        # 1) calculate the current tileset
        # 2) store the current parameters of interest
        # 3) update to newer parameters and calculate tileset
        # 4) revert parameters & return union of all tiles
        values0 = self.get_values(params)

        tiles0 = [self._tile(n) for n in particles]
        self.set_values(params, values)

        tiles1 = [self._tile(n) for n in particles]
        self.set_values(params, values0)

        return Tile.boundingtile(tiles0 + tiles1)

    @property
    def N(self):
        return self.rad.shape[0]

    def set_pos_rad(self, pos, rad):
        self.pos = pos.astype('float')
        self.rad = rad.astype('float')
        self.initialize()

    def set_draw_method(self, method, alpha=None, user_method=None):
        self.methods = [
            'lerp', 'logistic', 'triangle', 'constrained-cubic',
            'exact-gaussian', 'exact-gaussian-trim', 'exact-gaussian-fast',
            'user-defined'
        ]

        self.sphere_functions = {
            'bool': sphere_bool,
            'lerp': sphere_lerp,
            'logistic': sphere_logistic,
            'triangle': sphere_triangle_cdf,
            'exact-gaussian': sphere_analytical_gaussian,
            'exact-gaussian-trim': sphere_analytical_gaussian_trim,
            'exact-gaussian-fast': sphere_analytical_gaussian_fast,
            'constrained-cubic': sphere_constrained_cubic
        }

        self.alpha_defaults = {
            'bool': 0,
            'lerp': 0.4539,
            'logistic': 6.5,
            'triangle': 0.6618,
            'exact-gaussian': 0.27595,
            'exact-gaussian-trim': 0.27595,
            'exact-gaussian-fast': 0.27595,
            'constrained-cubic': 0.84990,
        }

        if user_method:
            self.sphere_functions['user-defined'] = user_method[0]
            self.alpha_defaults['user-defined'] = user_method[1]

        self.method = method
        if alpha is not None:
            self.alpha = tuple(listify(alpha))
        else:
            self.alpha = tuple(listify(self.alpha_defaults[self.method]))

    def _trans(self, pos):
        return pos + self.inner.l

    def _draw_particle(self, pos, rad, sign=1):
        # we can't draw 0 radius particles correctly, abort
        if rad == 0.0:
            return

        # translate to its actual position in the padded image
        pos = self._trans(pos)

        p = np.round(pos)
        r = np.round(np.array([1.0/self.zscale,1,1])*np.ceil(rad)+self.support_pad)

        tile = Tile(p-r, p+r, 0, self.shape.shape)
        rvec = self.rvecs[tile.slicer + (np.s_[:],)]

        # if required, do an iteration to find the best radius to produce
        # the goal volume as given by the particular goal radius
        if self.exact_volume:
            t = sign*exact_volume_sphere(
                rvec, pos, rad, zscale=self.zscale, volume_error=self.volume_error,
                function=self.sphere_functions[self.method], args=self.alpha,
                max_radius_change=self.max_radius_change
            )
        else:
            # calculate the anti-aliasing according to the interpolation type
            dr = inner(rvec, pos, rad, zscale=self.zscale)
            t = sign*self.sphere_functions[self.method](dr, rad, *self.alpha)

        self.particles[tile.slicer] += t

    def set_tile(self, tile):
        self.tile = tile

    def get_field(self):
        return self.particles[self.tile.slicer]

    def _vps(self, inds):
        return [j for j in inds if j >= 0 and j < self.N]

    def param_positions(self):
        """ Return params of all positions """
        return [self._i2p(i, j) for i in xrange(self.N) for j in ['x','y','z']]

    def param_radii(self):
        """ Return params of all radii """
        return [self._i2p(i, 'a') for i in xrange(self.N)]

    def param_particle(self, ind):
        """ Get position and radius of one or more particles """
        ind = self._vps(listify(ind))
        return [self._i2p(i, j) for i in ind for j in ['x', 'y', 'z', 'a']]

    def param_particle_pos(self, ind):
        """ Get position of one or more particles """
        ind = self._vps(listify(ind))
        return [self._i2p(i, j) for i in ind for j in ['x', 'y', 'z']]

    def param_particle_rad(self, ind):
        """ Get radius of one or more particles """
        ind = self._vps(listify(ind))
        return [self._i2p(i, 'a') for i in ind]

    def add_particle(self, pos, rad):
        """
        Add a particle at position pos (3 element list or numpy array) and
        radius rad (scalar float). Returns index of new particle.
        """
        if len(self.rad) == 0:
            self.pos = pos.reshape(-1, 3)
            self.rad = np.array([rad])
        else:
            self.pos = np.vstack([self.pos, pos])
            self.rad = np.hstack([self.rad, 0.0])

        # if we are not part of the system, go ahead and draw
        if not self._parent and self.shape:
            self._draw_particle(pos, rad, +1)

        # update the parameters globally
        self.setup_variables()
        self.trigger_parameter_change()
        ind = self.closest_particle(pos)

        # now request a drawing of the particle plz
        params = self.param_particle(ind)
        values = self.get_values(params)
        values[-1] = rad
        self.trigger_update(params, values)
        return ind

    def remove_particle(self, ind):
        """ Remove the particle at index `ind` """
        if self.rad.shape[0] == 0:
            return

        pos = self.pos[ind].copy()
        rad = self.rad[ind].copy()

        # draw it as zero size particle before changing parameters
        params = self.param_particle(ind)
        values = self.get_values(params)
        values[-1] = 0.0
        self.trigger_update(params, values)

        self.pos = np.delete(self.pos, ind, axis=0)
        self.rad = np.delete(self.rad, ind, axis=0)

        # if we are not part of the system, go ahead and draw
        if not self._parent and self.shape:
            self._draw_particle(pos, rad, -1)

        # update the parameters globally
        self.setup_variables()
        self.trigger_parameter_change()

    def get_positions(self):
        return self.pos

    def get_radii(self):
        return self.rad

    def closest_particle(self, x):
        """ Get the index of the particle closest to vector `x` """
        return (((self.pos - x)**2).sum(axis=-1)).argmin()

    def exports(self):
        return [
            self.add_particle, self.remove_particle, self.closest_particle,
            self.get_positions, self.get_radii
        ]

    def _i2p(self, ind, coord):
        """ Translate index info to parameter name """
        return '-'.join([self.param_prefix, str(ind), coord])

    def _p2i(self, param):
        """
        Parameter to indices, returns (type, index, coord). Therefore, for a
        pos    : (100, 'x')
        rad    : (100, 'a')
        zscale : ('zscale, None)
        """
        q = {'x': 2, 'y': 1, 'z': 0}
        g = param.split('-')
        if len(g) == 1:
            return 'zscale', None
        if len(g) == 3:
            return g[2], int(g[1])

    def _update_type(self, params):
        """ Returns dozscale and particle list of update """
        dozscale = False
        particles = []
        for p in listify(params):
            typ, ind = self._p2i(p)
            particles.append(ind)
            dozscale = dozscale or typ == 'zscale'
        particles = set(particles)
        return dozscale, particles

    def _tile(self, n):
        """ Get the tile surrounding particle `n` """
        zsc = np.array([1.0/self.zscale, 1, 1])
        pos, rad = self.pos[n], self.rad[n]
        pos = self._trans(pos)
        return Tile(pos - zsc*rad, pos + zsc*rad).pad(self.support_pad)

    def __str__(self):
        return "{} N={}".format(self.__class__.__name__, self.N)

    def __repr__(self):
        return self.__str__()

    def __getstate__(self):
        odict = self.__dict__.copy()
        cdd(odict, ['rvecs', 'particles', '_params'])
        return odict

    def __setstate__(self, idict):
        self.__dict__.update(idict)
        self.initialize()

#=============================================================================
# Coverslip half plane class
#=============================================================================
class Slab(Component):
    category = 'obj'

    def __init__(self, zpos=0, angles=(0,0), param_prefix='slab', shape=None):
        """
        A half plane corresponding to a cover-slip.

        Parameters:
        -----------
        shape : tuple
            field shape over which to calculate

        zpos : float
            position of the center of the slab in pixels

        angles : tuple of float (2,)
            angles of rotation of the normal wrt to z
        """
        self.lbl_zpos = param_prefix+'-zpos'
        self.lbl_theta = param_prefix+'-theta'
        self.lbl_phi = param_prefix+'-phi'

        self.shape = shape
        self.set_tile(self.shape)
        params = [self.lbl_zpos, self.lbl_theta, self.lbl_phi]
        values = [zpos, angles[0], angles[1]]
        super(Slab, self).__init__(params, values)

        if self.shape:
            self.initialize()

    def rmatrix(self):
        a0 = np.array([0,0,1])
        r0 = expm3(np.cross(np.eye(3), a0*self.param_dict[self.lbl_theta]))

        a1 = np.array([0,1,0])
        r1 = expm3(np.cross(np.eye(3), a1*self.param_dict[self.lbl_phi]))
        return np.dot(r1, r0)

    def normal(self):
        return np.dot(self.rmatrix(), np.array([1,0,0]))

    def _setup(self):
        self.rvecs = self.shape.coords(form='vector')
        self.image = np.zeros(self.shape.shape)

    def _draw_slab(self):
        # for the position at zpos, and the center in the x-y plane
        pos = np.array([
            self.param_dict[self.lbl_zpos], self.shape.shape[1]/2, self.shape.shape[2]/2
        ])
        pos = pos + self.inner.l

        p = (self.rvecs - pos).dot(self.normal())
        self.image = 1.0/(1.0 + np.exp(7*p))

    def initialize(self):
        self._setup()
        self._draw_slab()

    def set_tile(self, tile):
        self.tile = tile

    def update(self, params, values):
        super(Slab, self).update(params, values)
        self._draw_slab()

    def get_field(self):
        return self.image[self.tile.slicer]

    def get_update_tile(self, params, values):
        return self.shape.copy()

    def __getstate__(self):
        odict = self.__dict__.copy()
        cdd(odict, ['rvecs', 'image'])
        return odict

    def __setstate__(self, idict):
        self.__dict__.update(idict)
        self._setup()

    def __str__(self):
        return self.__repr__()

    def __repr__(self):
        return "{} <{}>".format(
            str(self.__class__.__name__), self.param_dict
        )

