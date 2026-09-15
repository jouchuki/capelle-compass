// Full policy recommendations from interpretation_capelle/full_policy_report.txt
// Source: interpretation_capelle/full_policy_report.txt
export const POLICY_DOMAINS = [
  {
    domain: 'Employment & Labor Market',
    age_group: 'Parents of children (25-55 years); Youth entering labor market (16-25 years)',
    impact: 'Parental employment stability directly affects household income, stress levels, and time available for child-rearing. Youth unemployment leads to school dropout.',
    levers: 'Employment programs, job training, workforce development',
    recommendations: [
      'Strengthen youth employment programs for 16-25 year olds',
      'Invest in vocational training (MBO/BBL) partnerships with local employers',
      'Provide subsidized internships and apprenticeships for at-risk youth',
      'Support parents\' job stability through skills retraining programs',
      'Monitor flexible vs. permanent employment ratio as indicator of household stability'
    ]
  },
  {
    domain: 'Income & Financial Security',
    age_group: 'All household members; especially families with children (0-18 years)',
    impact: 'Higher household income reduces child poverty, improves nutrition, housing quality, access to extracurricular activities, and educational resources.',
    levers: 'Income support, tax relief for families, minimum wage policies, benefits',
    recommendations: [
      'Expand targeted income support for households with school-age children',
      'Provide free or subsidized school supplies, meals, and activities',
      'Connect low-income families to existing benefits (toeslagen, kinderbijslag)',
      'Partner with debt counseling services (schuldhulpverlening)',
      'Offer financial literacy workshops for young parents'
    ]
  },
  {
    domain: 'Family Structure & Household Stability',
    age_group: 'Families with children (0-18); single parents (all ages)',
    impact: 'No-earner households are at highest risk for child poverty and school dropout. Dual-earner households generally provide more financial stability but may need affordable childcare to balance work and parenting.',
    levers: 'Childcare subsidies, parental leave, family support services',
    recommendations: [
      'Expand affordable childcare (kinderopvang) for dual-earner families',
      'Create targeted support programs for zero-earner households',
      'Provide re-employment coaching for non-working parents',
      'Support single-parent families with flexible childcare arrangements',
      'Facilitate community support networks (buurtzorg model)'
    ]
  },
  {
    domain: 'Youth Development & Education',
    age_group: 'Youth 12-25 years; at-risk students 14-18 years',
    impact: 'Early school leaving is both a symptom and a cause of reduced quality of life. Youth unemployment compounds this by removing career prospects.',
    levers: 'Dropout prevention, mentoring, vocational training, youth employment programs',
    recommendations: [
      'Implement early warning system for dropout risk (from age 12)',
      'Assign mentors/coaches to at-risk students in VMBO/MBO',
      'Create alternative education pathways (praktijkonderwijs, BBL)',
      'Work with RMC (Regionaal Meld- en Coördinatiepunt) for dropout tracking',
      'Provide career orientation (loopbaanoriëntatie) from age 14',
      'Address psychosocial issues (school social workers, GGD partnerships)'
    ]
  },
  {
    domain: 'Demographics & Population Dynamics',
    age_group: 'All ages; birth rate affects 0-4 year cohort; emigration affects working-age families',
    impact: 'Emigration of young families reduces the tax base and communal resources. Declining births may signal economic insecurity affecting family planning.',
    levers: 'Housing policy, integration programs, birth incentives, elderly care',
    recommendations: [
      'Monitor emigration of young families — may signal quality-of-life issues',
      'Ensure sufficient school capacity aligns with population projections',
      'Support immigrant families\' school integration (language, culture)',
      'Create \'child-friendly neighborhoods\' to retain young families'
    ]
  },
  {
    domain: 'Education Level of Population',
    age_group: 'Adults 25-65 (parental education); Youth 16-25 (own education)',
    impact: 'Higher parental education is one of the strongest predictors of child educational outcomes and overall well-being.',
    levers: 'Lifelong learning, adult education, vocational retraining',
    recommendations: [
      'Promote adult education (volwassenenonderwijs) for parents',
      'Invest in \'ouderbetrokkenheid\' programs (parental engagement in schools)',
      'Provide homework support (huiswerkbegeleiding) in community centers',
      'Create reading programs for families (Boekstart, Bibliotheek op school)'
    ]
  }
];

export const AGE_GROUPS = [
  {
    group: '0-4 years (Early childhood)',
    recommendations: [
      'Invest in VVE (Voor- en Vroegschoolse Educatie) programs',
      'Ensure access to quality childcare (kinderopvang)',
      'Support early language development, especially for immigrant families',
      'Provide parenting support (opvoedondersteuning) through CJG',
      'Home visits (huisbezoeken) for at-risk newborns'
    ]
  },
  {
    group: '4-12 years (Primary school)',
    recommendations: [
      'Fund full-time school programs (brede school/IKC concept)',
      'Provide subsidized sport and culture participation',
      'Implement anti-bullying programs (pestprotocol)',
      'Offer school meals (schoolontbijt, schoollunch) in low-income areas',
      'Identify learning difficulties early through regular GGD screenings',
      'Ensure trained support staff (intern begeleiders, schoolmaatschappelijk werk)'
    ]
  },
  {
    group: '12-16 years (Secondary school / VO)',
    recommendations: [
      'THIS IS THE CRITICAL WINDOW for dropout prevention',
      'Implement early warning systems based on attendance and grades',
      'Assign youth coaches (jongerencoaches) to at-risk students',
      'Create flexible learning paths for struggling students',
      'Address mental health through school counselors (decanen, mentoren)',
      'Provide career orientation (LOB) starting in year 1 of VO',
      'Engage parents of VO students (difficult but crucial age)'
    ]
  },
  {
    group: '16-18 years (MBO / upper VO)',
    recommendations: [
      'Highest dropout risk age — needs most intensive intervention',
      'Partner with MBO institutions for retention programs',
      'Provide practical learning opportunities (BBL, stages/internships)',
      'Offer financial support for students from low-income families',
      'Address transport barriers (OV subsidies for students)',
      'Create \'second chance\' pathways (tweedekansonderwijs)'
    ]
  },
  {
    group: '18-27 years (Young adults)',
    recommendations: [
      'Support young adults who left school early with re-entry programs',
      'Provide subsidized vocational certification (EVC, diploma-erkenning)',
      'Connect young adults to employment programs (UWV, municipality programs)',
      'Address housing issues that affect stability',
      'Offer life skills training (financial literacy, independent living)'
    ]
  }
];
